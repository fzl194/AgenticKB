"""Document Parse Operator（M4，SRS §4.6/§4.9/§4.10 / WP7+WP9 编排）.

驱动一次**质量门控的完整解析执行**：

```text
ParsePlan（primary + 有序 fallback + 预算）
  -> Run 状态机推进（QUEUED→INSPECTING→PLANNED→PARSING→…→EVALUATING）
  -> 每 backend 尝试：ShadowParseService.execute（读流→parse→normalize→
     reconcile→落 IR/raw 制品→质量评估）+ attempt 审计事件
  -> EVALUATING 决策（SRS §4.9）：
       PASS/WARN  -> pre-commit revision check -> SnapshotCommitService
                     （过期输入 → Run=SUPERSEDED，不建快照）
       FALLBACK   -> 预算内换链上下一后端重试
       REPAIR     -> M4 无修复执行器：有备选则降级为 FALLBACK，否则按
                     WARN 收尾（问题保留可见性，如实记录 repair_unavailable）
       FAIL       -> Run=FAILED（低质量不形成 READY Snapshot）
  -> SUCCEEDED（携带 snapshot_id）/ FAILED / SUPERSEDED
```

不负责：切片（M5）、workflow 算子挂接（M6）、Build 选择（M6）。

设计（ADR-0003 D-001/D-022）：
- 只依赖注入 Protocol；``parser_resolver`` 把 parser_id 解析为
  ``(parser, normalizer)`` 成对实例（组合根通常接 parse_adapters.factory）；
- 每 attempt 一个临时 ``ShadowParseService``（共享对象存储与仓储），
  复用其制品落存/replay 逻辑，不复制代码。
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from knowledge_mining.mining.contracts.file_management import (
    StorageObjectRepository,
)
from knowledge_mining.mining.contracts.parse_plan import ParsePlan
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import ObjectLocation
from knowledge_mining.mining.frozen_input.contracts import (
    FrozenInput,
    FrozenInputStale,
)
from knowledge_mining.mining.parse_quality.gate import (
    QualityDecision,
    QualityGate,
)
from knowledge_mining.mining.shadow_parse.contracts import (
    ParseAttemptRecord,
    ParseAttemptRepository,
    ParseRunRecord,
    ParseRunRepository,
)
from knowledge_mining.mining.shadow_parse.service import ShadowParseService
from knowledge_mining.mining.snapshot_store.service import SnapshotCommitService

logger = logging.getLogger(__name__)

ParserResolver = Callable[[str], tuple[Any, Any]]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class DocumentParseService:
    """质量门控解析编排器（Run 状态机 + attempt 审计 + 快照转正）."""

    def __init__(
        self,
        *,
        object_store: ObjectStorePort,
        parse_runs: ParseRunRepository,
        attempts: ParseAttemptRepository,
        storage_objects: StorageObjectRepository,
        parser_resolver: ParserResolver,
        commit_service: SnapshotCommitService,
        quality_gate: QualityGate | None = None,
        reconciler: Any | None = None,
        snapshots: Any | None = None,
        bucket_prefix: str | None = None,
        parse_bucket: str | None = None,
    ) -> None:
        self._store = object_store
        self._parse_runs = parse_runs
        self._attempts = attempts
        self._storage_objects = storage_objects
        self._resolver = parser_resolver
        self._commit = commit_service
        self._gate = quality_gate or QualityGate()
        self._reconciler = reconciler
        self._snapshots = snapshots  # 探针校验快照 lifecycle（可缺省）
        if not bucket_prefix and not parse_bucket:
            raise ValueError(
                "DocumentParseService requires a bucket prefix "
                "(bucket_prefix=... or parse_bucket=...); refusing to guess "
                "a default namespace"
            )
        self._bucket_prefix = bucket_prefix
        self._parse_bucket = parse_bucket

    # -- 入口 ---------------------------------------------------------------

    async def execute(
        self,
        frozen: FrozenInput,
        plan: ParsePlan,
        *,
        domain: str,
        source_text: str | None = None,
        title: str | None = None,
        run_id: str | None = None,
    ) -> ParseRunRecord:
        """按计划执行一次质量门控解析；返回终态 Run 记录."""
        primary_fp = self._resolver(plan.primary_parser_id)[0].descriptor.parser_fingerprint

        # 幂等探针（SRS §2.2）：同输入同 primary 的 SUCCEEDED Run 且快照仍在
        # → 直接复用，不重复解析。
        existing = await self._parse_runs.find_by_document_hash(
            frozen.document_id, frozen.source_raw_hash, primary_fp
        )
        if (
            existing is not None
            and existing.status == "SUCCEEDED"
            and existing.snapshot_id
            and await self._snapshot_reusable(existing.snapshot_id)
        ):
            return existing

        # 扫描件守卫（批次3-问题3）：无文本层 PDF 不进解析链，直接 FAILED 终态。
        rejection = await self._scanned_pdf_rejection(frozen)
        if rejection is not None:
            return await self._reject_scanned(frozen, plan, primary_fp, rejection)

        # 旧二进制 .doc（Word 97-2003）→ docx（2026-09-01 用户反馈）：v2 算子
        # 链此前无旧链 ingestion 预处理的等价物——registry 无 parser 支持
        # application/msword，plan 兜底 legacy_markdown 直接 UnsupportedFormat。
        try:
            frozen = await self._convert_legacy_doc(frozen)
        except Exception as exc:  # noqa: BLE001 —— 转换失败留痕为 FAILED 终态
            return await self._reject_scanned(
                frozen, plan, primary_fp,
                f"doc_to_docx_failed: {type(exc).__name__}: {exc}",
                guard="legacy_doc",
            )

        rid = run_id or _new_id("parse")
        await self._parse_runs.insert(ParseRunRecord(
            id=rid,
            document_id=frozen.document_id,
            source_storage_object_id=frozen.source_storage_object_id,
            source_raw_hash=frozen.source_raw_hash,
            source_content_revision=frozen.source_content_revision,
            parser_id=plan.primary_parser_id,
            parser_fingerprint=primary_fp,
            status="QUEUED",
            started_at=_utcnow(),
            metadata_json=json.dumps(
                {"mode": "m4-operator", "plan_id": plan.plan_id},
                ensure_ascii=False, sort_keys=True,
            ),
        ))
        await self._advance(rid, "INSPECTING")
        await self._advance(rid, "PLANNED")

        chain = plan.backend_chain()
        budget = plan.budget
        attempt_index = 0
        while attempt_index < len(chain) and attempt_index < budget.max_backend_attempts:
            parser_id = chain[attempt_index]
            kind = "primary" if attempt_index == 0 else "fallback"
            shadow = self._shadow_for(parser_id, plan.quality_profile)
            await self._advance(rid, "PARSING")
            attempt_started = _utcnow()
            try:
                outcome = await shadow.execute(
                    frozen,
                    source_text=source_text,
                    # S1 默认档仍只观测自动基准；strict 明确选择以该基准
                    # 参与门控。显式 source_text 保留调用方原有强制语义。
                    enforce_source_coverage=(
                        None if source_text is not None
                        else plan.quality_profile == "strict"
                    ),
                    budget=budget,
                    backend_attempts_used=attempt_index + 1,
                )
            except Exception as exc:  # noqa: BLE001 —— attempt 失败必须留档
                await self._attempts.append(self._attempt(
                    rid, attempt_index, parser_id, kind, "FAILED",
                    attempt_started, error_message=f"{type(exc).__name__}: {exc}",
                ))
                if attempt_index + 1 < len(chain) and attempt_index + 1 < budget.max_backend_attempts:
                    await self._advance(rid, "FALLING_BACK")
                    attempt_index += 1
                    continue
                await self._advance(
                    rid, "FAILED",
                    error_message=f"{type(exc).__name__}: {exc}",
                )
                return await self._final(rid)

            # 阶段如实推进（normalize/reconcile 已在 shadow.execute 内完成）。
            await self._record_quality(
                rid, attempt_index, parser_id, kind, outcome.quality_meta,
            )
            await self._advance(rid, "NORMALIZING")
            await self._advance(rid, "RECONCILING")
            await self._advance(
                rid, "EVALUATING",
                parse_ir_storage_object_id=outcome.parse_ir_storage_object_id,
                parse_ir_schema_version=outcome.document.schema_version,
                element_count=len(outcome.document.elements),
                container_count=len(outcome.document.containers),
                relation_count=len(outcome.document.relations),
            )
            decision = outcome.quality_decision
            effective = self._resolve_decision(
                decision,
                can_fallback=(attempt_index + 1 < len(chain)
                              and attempt_index + 1 < budget.max_backend_attempts),
            )

            if effective.decision in ("PASS", "WARN"):
                await self._attempts.append(self._attempt(
                    rid, attempt_index, parser_id, kind, "SUCCEEDED",
                    attempt_started,
                ))
                committed = await self._commit_or_supersede(
                    frozen, outcome, effective, rid, domain, title,
                )
                if committed is None:  # SUPERSEDED
                    return await self._final(rid)
                await self._advance(
                    rid, "SUCCEEDED", snapshot_id=committed.snapshot.id,
                    finished_at=_utcnow(),
                )
                return await self._final(rid)

            # FALLBACK / REPAIR / FAIL：attempt 本身按失败留档（质量拒绝）。
            await self._attempts.append(self._attempt(
                rid, attempt_index, parser_id, kind, "FAILED",
                attempt_started,
                error_message=f"quality: {effective.decision}"
                              + (f" ({'; '.join(i.code for i in effective.issues)})"
                                 if effective.issues else ""),
            ))
            if effective.decision in ("FALLBACK", "REPAIR"):
                if attempt_index + 1 < len(chain) and attempt_index + 1 < budget.max_backend_attempts:
                    await self._advance(rid, "FALLING_BACK")
                    attempt_index += 1
                    continue
            await self._advance(
                rid, "FAILED",
                error_message=(
                    f"quality decision {effective.decision}; no admissible "
                    f"attempt left in plan {plan.plan_id!r}"
                ),
                finished_at=_utcnow(),
            )
            return await self._final(rid)

        # 链耗尽仍未返回（理论不可达：循环内必达终态）。
        await self._advance(
            rid, "FAILED", error_message="backend chain exhausted"
        )
        return await self._final(rid)

    # -- replay（SRS §9.5 A09） ----------------------------------------------

    async def replay(
        self,
        frozen: FrozenInput,
        *,
        backend_raw_storage_object_id: str,
        parser_id: str,
        domain: str,
        normalizer: Any | None = None,
        source_text: str | None = None,
        title: str | None = None,
        run_id: str | None = None,
    ) -> ParseRunRecord:
        """用已持久化的 backend raw artifact 重放 normalize 并转正新快照.

        parser **不被调用**（A09：修复 adapter mapping bug 不重复花钱）。
        ``normalizer`` 覆盖注入升级版实现——新指纹 → 新 Snapshot。
        """
        parser, resolved_norm = self._resolver(parser_id)
        norm = normalizer or resolved_norm
        artifact = await self._load_raw_artifact(backend_raw_storage_object_id)

        # 扫描件守卫（批次3-问题3）：无文本层 PDF 不进解析链，直接 FAILED 终态。
        rejection = await self._scanned_pdf_rejection(frozen)
        if rejection is not None:
            return await self._reject_scanned(frozen, plan, primary_fp, rejection)

        # 旧二进制 .doc（Word 97-2003）→ docx（2026-09-01 用户反馈）：v2 算子
        # 链此前无旧链 ingestion 预处理的等价物——registry 无 parser 支持
        # application/msword，plan 兜底 legacy_markdown 直接 UnsupportedFormat。
        try:
            frozen = await self._convert_legacy_doc(frozen)
        except Exception as exc:  # noqa: BLE001 —— 转换失败留痕为 FAILED 终态
            return await self._reject_scanned(
                frozen, plan, primary_fp,
                f"doc_to_docx_failed: {type(exc).__name__}: {exc}",
                guard="legacy_doc",
            )

        rid = run_id or _new_id("parse")
        await self._parse_runs.insert(ParseRunRecord(
            id=rid,
            document_id=frozen.document_id,
            source_storage_object_id=frozen.source_storage_object_id,
            source_raw_hash=frozen.source_raw_hash,
            source_content_revision=frozen.source_content_revision,
            parser_id=parser_id,
            parser_fingerprint=parser.descriptor.parser_fingerprint,
            status="QUEUED",
            started_at=_utcnow(),
            metadata_json=json.dumps(
                {"mode": "m4-operator", "replay_of": backend_raw_storage_object_id},
                ensure_ascii=False, sort_keys=True,
            ),
        ))
        await self._advance(rid, "INSPECTING")
        await self._advance(rid, "PLANNED")
        await self._advance(rid, "PARSING")  # 重放无 parse 工作，状态如实走过
        attempt_started = _utcnow()
        shadow = self._shadow_for(parser_id)
        try:
            outcome = await shadow.replay(
                artifact,
                source_raw_hash=frozen.source_raw_hash,
                normalizer=norm,
                source_text=source_text,
            )
        except Exception as exc:  # noqa: BLE001
            await self._attempts.append(self._attempt(
                rid, 0, parser_id, "replay", "FAILED", attempt_started,
                error_message=f"{type(exc).__name__}: {exc}",
            ))
            await self._advance(
                rid, "FAILED", error_message=f"{type(exc).__name__}: {exc}"
            )
            return await self._final(rid)

        await self._record_quality(
            rid, 0, parser_id, "replay", outcome.quality_meta,
        )
        await self._advance(rid, "NORMALIZING")
        await self._advance(rid, "RECONCILING")
        await self._advance(
            rid, "EVALUATING",
            parse_ir_storage_object_id=outcome.parse_ir_storage_object_id,
            parse_ir_schema_version=outcome.document.schema_version,
            element_count=len(outcome.document.elements),
            container_count=len(outcome.document.containers),
            relation_count=len(outcome.document.relations),
        )
        await self._attempts.append(self._attempt(
            rid, 0, parser_id, "replay", "SUCCEEDED", attempt_started,
        ))
        effective = self._resolve_decision(outcome.quality_decision)
        if effective.decision not in ("PASS", "WARN"):
            await self._advance(
                rid, "FAILED",
                error_message=(
                    f"quality decision {effective.decision} on replay"
                ),
                finished_at=_utcnow(),
            )
            return await self._final(rid)
        committed = await self._commit_or_supersede(
            frozen, outcome, effective, rid, domain, title,
        )
        if committed is None:
            return await self._final(rid)
        await self._advance(
            rid, "SUCCEEDED", snapshot_id=committed.snapshot.id,
            finished_at=_utcnow(),
        )
        return await self._final(rid)

    # -- 内部 ---------------------------------------------------------------

    async def _snapshot_reusable(self, snapshot_id: str) -> bool:
        """对抗评审 MED-1：探针复用前校验快照仍 READY（REVOKED/
        DEPRECATED 不复用，落入完整重跑）。未注入 snapshots 时保守放行
        （M2 兼容）。"""
        if self._snapshots is None:
            return True
        snap = await self._snapshots.get(snapshot_id)
        return snap is not None and snap.lifecycle_status == "READY"

    def _shadow_for(
        self, parser_id: str, quality_profile: str = "default"
    ) -> ShadowParseService:
        """为一次尝试组装 ShadowParseService（共享存储/仓储，换 parser）."""
        parser, normalizer = self._resolver(parser_id)
        return ShadowParseService(
            object_store=self._store,
            parse_runs=self._parse_runs,
            storage_objects=self._storage_objects,
            parser=parser,
            normalizer=normalizer,
            reconciler=self._reconciler,
            quality_gate=self._gate_for(quality_profile),
            bucket_prefix=self._bucket_prefix,
            parse_bucket=self._parse_bucket,
        )

    def _gate_for(self, quality_profile: str) -> QualityGate:
        """默认保留注入 Gate；命名档位按冻结 Plan 生成阈值快照。"""
        if quality_profile == "default":
            return self._gate
        from knowledge_mining.mining.parse_quality.gate import (
            quality_profile_for,
        )

        return QualityGate(profile=quality_profile_for(quality_profile))

    #: 扫描件守卫的字节缓存（批次3-问题3）：source 对象不可变，检测结果
    #: 进程内复用——同一文档重复挖掘/重放不再重复读对象。
    _pdf_bytes_cache: dict = {}
    _PDF_BYTES_CACHE_MAX = 32

    async def _scanned_pdf_rejection(self, frozen) -> str | None:
        """无文本层 PDF 直接明确拒绝（批次3-问题3：file_inspector 接线）。

        has_text_layer 检测此前只在未挂线的路由模块——生产链由 registry
        顺序直接构链，扫描件靠质量 FAIL"巧合"兜住且报错误导（empty_document）。
        接线后：不进解析链、给明确的"需 OCR"信息、attempt 完整留痕。
        """
        if (frozen.mime or "").lower() != "application/pdf":
            return None
        data = self._pdf_bytes_cache.get(frozen.source_storage_object_id)
        if data is None:
            chunks = []
            async for chunk in self._store.get_stream(ObjectLocation(
                    bucket=frozen.bucket, object_key=frozen.object_key,
                    version_id=frozen.object_version_id)):
                chunks.append(chunk)
            data = b"".join(chunks)
            self._pdf_bytes_cache[frozen.source_storage_object_id] = data
            while len(self._pdf_bytes_cache) > self._PDF_BYTES_CACHE_MAX:
                self._pdf_bytes_cache.pop(next(iter(self._pdf_bytes_cache)))
        try:
            from knowledge_mining.mining.file_inspector.inspect import FileInspector
            profile = FileInspector().inspect(data, declared_mime=frozen.mime)
        except Exception:  # noqa: BLE001 —— 检测失败不拦解析（让解析链自己报）
            return None
        if profile.has_text_layer is False:
            return (
                "scanned_pdf_needs_ocr: 扫描件 PDF（无文本层）暂不支持解析——"
                "需 OCR 能力（未上线）。请上传文本版 PDF，或先用 OCR 工具转换。"
            )
        return None

    #: docx MIME（application/vnd.openxmlformats-officedocument.wordprocessingml.document）
    _DOCX_MIME = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    async def _convert_legacy_doc(self, frozen: FrozenInput) -> FrozenInput:
        """application/msword（.doc）→ docx 后重定向 frozen（原地无副作用）。

        非-msword 原样返回。转换走 LibreOffice（doc_to_docx，与旧链同一
        实现）；产物按源内容哈希命名写回对象存储（``原key--doc2docx-<hash>.docx``），
        **原始对象不动**——下载/审计仍取原件。幂等：产物对象已存在（同源
        hash 重复挖掘）直接复用，不重复转换。

        ``source_raw_hash``/``size`` 更新为**转换产物**的实际值——shadow
        reader 按本字段校验流字节；幂等探针键（document_id, source_raw_hash,
        parser_fp）随之绑定转换产物（转换器版本变化 → 产物变化 → 重解析，
        语义正确）。
        """
        if (frozen.mime or "").lower() != "application/msword":
            return frozen
        from knowledge_mining.mining.ingestion.doc_preprocessing import (
            doc_to_docx,
        )
        from knowledge_mining.mining.contracts.storage.types import PutOptions

        source = ObjectLocation(
            bucket=frozen.bucket, object_key=frozen.object_key,
            version_id=frozen.object_version_id,
        )
        target_key = f"{frozen.object_key}--doc2docx-{frozen.source_raw_hash[:16]}.docx"
        target = ObjectLocation(bucket=frozen.bucket, object_key=target_key)

        import hashlib
        docx_bytes: bytes
        new_hash: str
        try:
            existing = await self._store.stat(target)
        except Exception:
            existing = None
        if existing is not None and existing.sha256:
            new_hash = existing.sha256
            docx_size = existing.size
        else:
            data = b"".join(
                [chunk async for chunk in self._store.get_stream(source)]
            )
            import tempfile
            from pathlib import Path
            tmp_in = Path(tempfile.mkstemp(suffix=".doc")[1])
            try:
                tmp_in.write_bytes(data)
                converted = doc_to_docx(tmp_in)
                docx_bytes = converted.read_bytes()
            finally:
                tmp_in.unlink(missing_ok=True)
            new_hash = hashlib.sha256(docx_bytes).hexdigest()

            async def _one_shot():
                yield docx_bytes

            await self._store.put_stream(
                target, _one_shot(),
                PutOptions(
                    artifact_class="derived",
                    mime=self._DOCX_MIME,
                    content_length=len(docx_bytes),
                    expected_sha256=new_hash,
                    metadata={"converted_from": "application/msword",
                              "source_sha256": frozen.source_raw_hash},
                ),
            )
            docx_size = len(docx_bytes)

        from dataclasses import replace as _replace
        return _replace(
            frozen,
            mime=self._DOCX_MIME,
            object_key=target_key,
            object_version_id=None,
            source_raw_hash=new_hash,
            size=docx_size,
        )

    async def _reject_scanned(self, frozen, plan, primary_fp: str,
                              message: str, *, guard: str = "scanned_pdf"):
        """扫描件守卫的失败终态：run + attempt 完整留痕（不走解析链）。"""
        rid = _new_id("parse")
        await self._parse_runs.insert(ParseRunRecord(
            id=rid,
            document_id=frozen.document_id,
            source_storage_object_id=frozen.source_storage_object_id,
            source_raw_hash=frozen.source_raw_hash,
            source_content_revision=frozen.source_content_revision,
            parser_id=plan.primary_parser_id,
            parser_fingerprint=primary_fp,
            status="QUEUED",
            started_at=_utcnow(),
            metadata_json=json.dumps(
                {"mode": "m4-operator", "plan_id": plan.plan_id,
                 "guard": guard},
                ensure_ascii=False, sort_keys=True,
            ),
        ))
        await self._advance(rid, "INSPECTING")
        await self._advance(rid, "PLANNED")
        await self._advance(rid, "PARSING")  # 状态机：FAILED 只能从 PARSING 进入
        await self._attempts.append(self._attempt(
            rid, 0, plan.primary_parser_id, "primary", "FAILED", _utcnow(),
            error_message=f"{guard}: {message}",
        ))
        await self._advance(rid, "FAILED", error_message=message,
                            finished_at=_utcnow())
        return await self._final(rid)

    def _resolve_decision(
        self, decision: QualityDecision | None, *, can_fallback: bool = False
    ) -> QualityDecision:
        """REPAIR 的 M4 降级策略：无修复执行器 → 保守 WARN（问题保留）.

        有后续备选时上层已按 FALLBACK 处理；此处只兜「无备选」的 REPAIR
        ——空页信号不应阻断可用结果，但必须以 WARN + issue 可见。
        """
        if decision is None:
            from knowledge_mining.mining.parse_quality.gate import (
                QualityIssue,
            )

            return QualityDecision(decision="WARN", issues=(QualityIssue(
                code="quality_gate_not_injected",
                message="no quality gate wired; committing with WARN",
            ),), metrics=None)
        if decision.decision != "REPAIR":
            return decision
        if can_fallback:
            from knowledge_mining.mining.parse_quality.gate import FallbackRequest

            return QualityDecision(
                decision="FALLBACK", issues=decision.issues,
                metrics=decision.metrics,
                fallback_request=FallbackRequest(reason_codes=("repair_unavailable",)),
            )
        from knowledge_mining.mining.parse_quality.gate import QualityIssue

        return QualityDecision(
            decision="WARN",
            issues=decision.issues + (QualityIssue(
                code="repair_unavailable",
                message=(
                    "gate requested REPAIR but M4 has no repair executor; "
                    "committed with WARN visibility"
                ),
            ),),
            metrics=decision.metrics,
        )

    async def _record_quality(
        self,
        rid: str,
        attempt_index: int,
        parser_id: str,
        attempt_kind: str,
        quality_meta: dict[str, Any],
    ) -> None:
        """将每次已完成解析的质量结论追加到 Run 投影。"""
        if not quality_meta:
            return
        record = await self._parse_runs.get(rid)
        assert record is not None, f"parse run {rid} disappeared mid-flight"
        try:
            current = json.loads(record.metadata_json or "{}")
        except json.JSONDecodeError:
            current = {}
        if not isinstance(current, dict):
            current = {}
        existing_attempts = current.get("quality_attempts")
        attempts = list(existing_attempts) if isinstance(existing_attempts, list) else []
        entry = {
            "attempt_index": attempt_index,
            "parser_id": parser_id,
            "attempt_kind": attempt_kind,
            "decision": quality_meta.get("quality_decision"),
            "issues": quality_meta.get("quality_issues", []),
            "metrics": quality_meta.get("quality_metrics", {}),
        }
        metadata = {**current, "quality_attempts": [*attempts, entry]}
        await self._parse_runs.update_metadata(
            rid, json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        )

    async def _commit_or_supersede(
        self,
        frozen: FrozenInput,
        outcome: Any,
        decision: QualityDecision,
        rid: str,
        domain: str,
        title: str | None,
    ) -> Any | None:
        """提交快照；过期输入 → Run=SUPERSEDED 并返回 None（不建快照）."""
        try:
            return await self._commit.commit(
                frozen=frozen,
                document=outcome.document,
                parse_ir_storage_object_id=outcome.parse_ir_storage_object_id,
                quality_decision=decision,
                run_id=rid,
                domain=domain,
                title=title,
            )
        except FrozenInputStale as stale:
            await self._advance(
                rid, "SUPERSEDED",
                error_message=(
                    f"frozen input stale: document revision moved "
                    f"{stale.frozen_revision} -> {stale.current_revision} "
                    f"during parse; snapshot suppressed"
                ),
                finished_at=_utcnow(),
            )
            return None
        except Exception as exc:  # noqa: BLE001 —— 提交期基础设施异常不得卡死 Run
            # 对抗评审 HIGH-1：DB 断连/对象缺失等非预期异常 → 终态 FAILED
            # （§9.2 终态保证；卡 EVALUATING 无法自愈）。
            await self._advance(
                rid, "FAILED",
                error_message=(
                    f"snapshot commit failed: {type(exc).__name__}: {exc}"
                ),
                finished_at=_utcnow(),
            )
            return None

    async def _load_raw_artifact(self, storage_object_id: str) -> Any:
        """回读持久化的 backend raw artifact 并重建（§9.5 replay 原料）."""
        from knowledge_mining.mining.contracts.parser_adapter import (
            BackendParseArtifact,
        )
        from knowledge_mining.mining.contracts.storage.errors import (
            StorageObjectMissing,
        )

        record = await self._storage_objects.get(storage_object_id)
        if record is None:
            raise StorageObjectMissing(
                f"backend raw artifact {storage_object_id!r} is not registered"
            )
        location = ObjectLocation(
            bucket=record.bucket, object_key=record.object_key,
            version_id=record.object_version_id,
        )
        chunks: list[bytes] = []
        async for chunk in self._store.get_stream(location):
            chunks.append(chunk)
        payload = b"".join(chunks)
        return BackendParseArtifact.from_dict(json.loads(payload))

    def _attempt(
        self,
        rid: str,
        index: int,
        parser_id: str,
        kind: str,
        outcome: str,
        started_at: str,
        *,
        error_message: str | None = None,
    ) -> ParseAttemptRecord:
        return ParseAttemptRecord(
            id=_new_id("att"),
            parse_run_id=rid,
            attempt_index=index,
            parser_id=parser_id,
            parser_fingerprint=(
                self._resolver(parser_id)[0].descriptor.parser_fingerprint
            ),
            attempt_kind=kind,
            outcome=outcome,
            started_at=started_at,
            finished_at=_utcnow(),
            error_message=error_message,
        )

    async def _advance(
        self,
        rid: str,
        status: str,
        *,
        error_message: str | None = None,
        snapshot_id: str | None = None,
        finished_at: str | None = None,
        parse_ir_storage_object_id: str | None = None,
        parse_ir_schema_version: str | None = None,
        element_count: int | None = None,
        container_count: int | None = None,
        relation_count: int | None = None,
    ) -> None:
        await self._parse_runs.set_status(
            rid, status,
            error_message=error_message,
            snapshot_id=snapshot_id,
            finished_at=finished_at,
            parse_ir_storage_object_id=parse_ir_storage_object_id,
            parse_ir_schema_version=parse_ir_schema_version,
            element_count=element_count,
            container_count=container_count,
            relation_count=relation_count,
        )

    async def _final(self, rid: str) -> ParseRunRecord:
        record = await self._parse_runs.get(rid)
        assert record is not None, f"run {rid} disappeared mid-flight"
        return record


__all__ = ["DocumentParseService", "ParserResolver"]
