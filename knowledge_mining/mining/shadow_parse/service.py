"""Shadow Parse orchestration service (M2, SRS §2.2 / §C08 / §4.6).

``ShadowParseService`` 驱动一次影子解析执行，全程与现有发布链路硬隔离：

```text
FrozenInput（SRS §3.2 冻结输入）
  -> ObjectStoreSourceArtifactReader.open_stream   # 流式读 + sha256 校验
  -> decode utf-8（严格）                            # 坏字节即 FAILED
  -> DocumentParser.parse(text, mime)               # 注入的 parser Protocol（§C06）
  -> ParseIRNormalizer.normalize(...)               # 注入的 normalizer Protocol（§C07）
  -> ParsedDocument.to_dict() -> JSON               # 制品字节
  -> ObjectStorePort.put_stream（parse bucket）     # artifact_class=parse_ir
  -> StorageObjectRepository.register（先 find_by_location 去重，D-002）
  -> ParseRunRepository.upsert（SUCCEEDED 投影行）
```

边界纪律（M2 退出条件）：
- **绝不写** ``asset_document_snapshots`` / ``asset_raw_segments`` /
  ``mining_run_documents`` —— 影子链路只落 Parse IR 制品与 ``asset_parse_runs``
  投影，不影响现有发布。
- 只依赖注入的 Protocol（parser / normalizer / ObjectStorePort / 两个仓储），
  **不 import 任何具体 parse_adapters 实现**（该包由适配器侧并行开发）。

幂等（SRS §2.2）：``run`` 先做幂等探针——命中同
``(document_id, source_raw_hash, parser_fingerprint)`` 的 SUCCEEDED 投影行时
直接返回 ``reused=True``，不重复解析、不写对象。

错误处理：读流 hash 不匹配（``StorageObjectCorrupt``）、解码失败（包装为
``ValueError``）、parse / normalize 异常，均先落一行 FAILED 投影（含
``error_message``）再原样 re-raise，调用方决定重试策略。

References:
- SRS §2.2（幂等复用）、§4.6（一次解析执行）、§C08（Shadow Parse）。
- ADR-0003 D-002（内容寻址去重）、D-020（location 寻址）、D-022（服务分层）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge_mining.mining.contracts.file_management import (
    StorageObjectRecord,
    StorageObjectRepository,
)
from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument
from knowledge_mining.mining.contracts.parser_adapter import (
    DocumentParser,
    ParseIRNormalizer,
)
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import (
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.frozen_input.contracts import FrozenInput
from knowledge_mining.mining.frozen_input.source_reader import (
    ObjectStoreSourceArtifactReader,
)
from knowledge_mining.mining.infra.object_store.keys import build_object_key
from knowledge_mining.mining.shadow_parse.contracts import (
    ParseRunRecord,
    ParseRunRepository,
    ShadowParseResult,
)

# IR 制品上传分块大小（与 FakeObjectStore / source_reader 的 64 KiB 一致）。
_CHUNK_SIZE = 64 * 1024

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttemptOutcome:
    """一次 backend 尝试的完整产物（供 Parse Operator 编排消费，M4）.

    ``quality_decision`` 为 None 表示未注入 quality gate（M2 兼容）；
    ``quality_meta`` 是进投影 metadata 的 JSON 片段（决策/issues/指标）。
    """

    document: ParsedDocument
    artifact: Any
    parse_ir_storage_object_id: str
    quality_decision: Any | None = None
    quality_meta: dict[str, Any] = dataclass_field(default_factory=dict)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _text_baseline(mime: str, data: bytes) -> str | None:
    """从已聚合的冻结文本字节构造覆盖率基准，不增加对象存储读取。"""
    if mime.lower() not in {"text/markdown", "text/plain"}:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # 基准不可得不应阻断解析；解析器仍按自己的解码策略处理输入。
        return None


async def _chunked(payload: bytes) -> AsyncIterator[bytes]:
    """把已物化的制品字节切成 64 KiB 块喂给 ``put_stream``。"""
    for offset in range(0, len(payload), _CHUNK_SIZE):
        yield payload[offset : offset + _CHUNK_SIZE]


class ShadowParseService:
    """编排一次影子解析执行（SRS §C08；只进影子链路，不进发布）。

    整改轮（2026-08-17）扩展主线两环：
    - **backend raw artifact 持久化**：``BackendParseArtifact`` 序列化落
      parse bucket（artifact_class=backend_raw），供 normalizer 升级后
      **replay**（§9.5 "adapter mapping bug" 行——不重跑昂贵 parser）；
    - **Reconciler / Quality Gate 可选注入**：normalize 后接
      ``reconciler.reconcile``（文档级规则，C08）与 ``compute_metrics`` +
      ``quality_gate.evaluate``（C09），决策进投影 metadata。
    """

    def __init__(
        self,
        *,
        object_store: ObjectStorePort,
        parse_runs: ParseRunRepository,
        storage_objects: StorageObjectRepository,
        parser: DocumentParser,
        normalizer: ParseIRNormalizer,
        reader: ObjectStoreSourceArtifactReader | None = None,
        config: Any | None = None,
        bucket_prefix: str | None = None,
        parse_bucket: str | None = None,
        reconciler: Any | None = None,
        quality_gate: Any | None = None,
    ) -> None:
        self._store = object_store
        self._parse_runs = parse_runs
        self._storage_objects = storage_objects
        self._parser = parser
        self._normalizer = normalizer
        self._reconciler = reconciler
        self._quality_gate = quality_gate
        self._reader = reader or ObjectStoreSourceArtifactReader(
            object_store,
            Path(tempfile.gettempdir()) / "shadow_parse",
        )
        # bucket 解析优先级：显式 parse_bucket > bucket_prefix > config.bucket_prefix。
        # 无任何配置时 fail-fast：环境前缀不可猜测（写错命名空间 = 制品落错租户）。
        prefix = (
            bucket_prefix
            or getattr(config, "bucket_prefix", None)
        )
        if not prefix and not parse_bucket:
            raise ValueError(
                "ShadowParseService requires a bucket prefix (bucket_prefix=... "
                "or parse_bucket=...); refusing to guess a default namespace"
            )
        self._parse_bucket_name = parse_bucket or f"{prefix}parse"

    # -- 入口 ---------------------------------------------------------------

    async def run(
        self,
        frozen: FrozenInput,
        *,
        parse_run_id: str | None = None,
    ) -> ShadowParseResult:
        """执行一次影子解析；返回 :class:`ShadowParseResult`。

        幂等探针命中已有 SUCCEEDED 投影时直接返回 ``reused=True``（SRS §2.2）。
        任何失败先落 FAILED 投影行再 re-raise 原异常。
        """
        descriptor = self._parser.descriptor
        existing = await self._parse_runs.find_by_document_hash(
            frozen.document_id, frozen.source_raw_hash, descriptor.parser_fingerprint
        )
        if existing is not None and existing.status == "SUCCEEDED":
            reusable = await self._ir_object_available(
                existing.parse_ir_storage_object_id
            )
            if reusable:
                return ShadowParseResult(
                    parse_run_id=existing.id,
                    status="SUCCEEDED",
                    parse_ir_storage_object_id=existing.parse_ir_storage_object_id,
                    element_count=existing.element_count,
                    reused=True,
                )
            # 投影行成功但制品对象缺失（SRS §8.6 完整性事故）：不复用，
            # 落入完整重跑——内容寻址重放同字节后经 upsert 幂等回到原行。

        run_id = parse_run_id or _new_id("parse")
        started_at = _utcnow()
        try:
            outcome = await self.execute(frozen)
        except Exception as exc:  # noqa: BLE001 —— 影子运行必须落 FAILED 后透传
            try:
                await self._record_failure(frozen, run_id, started_at, exc)
            except Exception:  # noqa: BLE001 —— 审计落库失败不得吞掉原始异常
                logger.exception(
                    "failed to persist FAILED projection for run %s", run_id
                )
            raise
        record = await self._record_success(
            frozen, run_id, started_at, outcome.document,
            outcome.parse_ir_storage_object_id, outcome.quality_meta,
        )
        return ShadowParseResult(
            parse_run_id=record.id,
            status="SUCCEEDED",
            parse_ir_storage_object_id=outcome.parse_ir_storage_object_id,
            element_count=record.element_count,
            reused=False,
        )

    # -- 尝试执行（M4：供 Parse Operator 编排消费） --------------------------

    async def execute(
        self,
        frozen: FrozenInput,
        *,
        source_text: str | None = None,
        enforce_source_coverage: bool | None = None,
        budget: Any | None = None,
        backend_attempts_used: int = 0,
    ) -> AttemptOutcome:
        """执行一次 backend 尝试（读流→parse→normalize→reconcile→落制品→评估）.

        与 ``run`` 的区别：不写 ``asset_parse_runs`` 投影、不做幂等探针
        ——Run 生命周期与 attempt 审计由调用方（DocumentParseService）拥有。
        """
        doc, artifact, source_bytes = await self._read_and_parse(frozen)
        source_text_was_supplied = source_text is not None
        effective_source_text = source_text
        if effective_source_text is None:
            effective_source_text = _text_baseline(frozen.mime, source_bytes)
        return await self._finish(
            doc, artifact, source_text=effective_source_text,
            enforce_source_coverage=(
                source_text_was_supplied
                if enforce_source_coverage is None
                else enforce_source_coverage
            ),
            budget=budget,
            backend_attempts_used=backend_attempts_used,
        )

    async def replay(
        self,
        artifact: Any,
        *,
        source_raw_hash: str,
        normalizer: Any | None = None,
        source_text: str | None = None,
        budget: Any | None = None,
        backend_attempts_used: int = 0,
    ) -> AttemptOutcome:
        """从 backend raw artifact 重放 normalize（不重跑 parser，§9.5 A09）.

        ``normalizer`` 覆盖注入实现"normalizer 升级后重放"（升级版产出新
        指纹 → 新 Snapshot）；缺省用构造时注入的 normalizer。
        """
        norm = normalizer or self._normalizer
        doc = await asyncio.to_thread(
            norm.normalize, artifact, source_raw_hash=source_raw_hash,
        )
        return await self._finish(
            doc, artifact, source_text=source_text,
            enforce_source_coverage=source_text is not None,
            budget=budget,
            backend_attempts_used=backend_attempts_used,
        )

    async def _finish(
        self,
        doc: ParsedDocument,
        artifact: Any,
        *,
        source_text: str | None = None,
        enforce_source_coverage: bool = False,
        budget: Any | None = None,
        backend_attempts_used: int = 0,
    ) -> AttemptOutcome:
        """reconcile → 落 IR/raw 制品 → 质量评估（execute/replay 共用尾段）."""
        if self._reconciler is not None:
            outcome = await asyncio.to_thread(self._reconciler.reconcile, doc)
            doc = getattr(outcome, "document", outcome)
        parse_ir_object_id = await self._persist_ir(doc)
        await self._persist_raw_artifact(artifact)
        decision, quality_meta = self._evaluate_quality(
            doc,
            source_text=source_text,
            enforce_source_coverage=enforce_source_coverage,
            budget=budget,
            backend_attempts_used=backend_attempts_used,
        )
        return AttemptOutcome(
            document=doc,
            artifact=artifact,
            parse_ir_storage_object_id=parse_ir_object_id,
            quality_decision=decision,
            quality_meta=quality_meta,
        )

    # -- replay（SRS §9.5 A09，M2 兼容入口） ----------------------------------

    async def renormalize(
        self,
        artifact: Any,
        *,
        source_raw_hash: str,
    ) -> ParsedDocument:
        """从 backend raw artifact 重新 normalize（不重跑 parser）.

        §9.5 "adapter mapping bug" 恢复行：normalizer 升级后用持久化的
        raw artifact 重放转换，不重复调用（昂贵的）parser/云 API。
        """
        outcome = await self.replay(artifact, source_raw_hash=source_raw_hash)
        return outcome.document

    # -- 读取 + 解析 --------------------------------------------------------

    async def _ir_object_available(self, storage_object_id: str | None) -> bool:
        """幂等复用前置校验：IR 对象注册行在且字节确实在（SRS §2.2/§8.6）。"""
        if not storage_object_id:
            return False
        record = await self._storage_objects.get(storage_object_id)
        if record is None:
            return False
        available = await self._store.head_exists(
            ObjectLocation(bucket=record.bucket, object_key=record.object_key)
        )
        if available:
            await self._storage_objects.mark_verified(record.id, _utcnow())
        return available

    async def _read_and_parse(
        self, frozen: FrozenInput
    ) -> tuple[ParsedDocument, Any, bytes]:
        """流式读冻结对象（sha256 增量校验）、严格解码、parse + normalize。

        - parse 是同步 CPU 工作，经 ``asyncio.to_thread`` 下放（D-021 惯例），
          避免大文档阻塞事件循环。
        - normalize **不传 parse_run_id**：IR 制品字节必须对同一输入完全确定，
          否则内容寻址去重（D-002 / §2.2 幂等）永远 miss；run 归属只记录在
          ``asset_parse_runs`` 投影行，不进制品。
        - 返回 IR、backend artifact 与已读取源字节，后者供质量基准复用。
        """
        chunks: list[bytes] = []
        async for chunk in self._reader.open_stream(frozen):
            chunks.append(chunk)
        data = b"".join(chunks)
        artifact = await asyncio.to_thread(self._parser.parse, data, mime=frozen.mime)
        doc = await asyncio.to_thread(
            self._normalizer.normalize,
            artifact,
            source_raw_hash=frozen.source_raw_hash,
        )
        return doc, artifact, data

    def _evaluate_quality(
        self,
        doc: ParsedDocument,
        *,
        source_text: str | None = None,
        enforce_source_coverage: bool = False,
        budget: Any | None = None,
        backend_attempts_used: int = 0,
    ) -> tuple[Any | None, dict[str, Any]]:
        """质量门禁评估（C09）；未注入 gate 时返回 (None, {})（影子观测不阻断）."""
        if self._quality_gate is None:
            return None, {}
        from knowledge_mining.mining.parse_quality import (
            compute_metrics,
            quality_metrics_to_dict,
        )

        metrics = compute_metrics(doc, source_text=source_text)
        # S1 自动基准先用于观测：现有无 fallback 的生产链不能因历史解析
        # 器的文本归一化差异被批量阻断。调用方显式传入基准时仍保留既有强制
        # 门控语义；S2/S3 再把自动基准接入策略档与实际 fallback 链。
        gate_metrics = metrics
        if source_text is not None and not enforce_source_coverage:
            from dataclasses import replace

            gate_metrics = replace(metrics, char_coverage=None)
        decision = self._quality_gate.evaluate(
            gate_metrics,
            budget=budget,
            backend_attempts_used=backend_attempts_used,
        )
        if gate_metrics is not metrics:
            from dataclasses import replace

            decision = replace(decision, metrics=metrics)
        meta = {
            "quality_decision": decision.decision,
            "quality_issues": [
                {"code": i.code, "message": i.message}
                for i in decision.issues
            ],
            "quality_metrics": quality_metrics_to_dict(metrics),
        }
        return decision, meta

    # -- 制品落存（parse bucket + 对象注册） ---------------------------------

    async def _persist_ir(self, doc: ParsedDocument) -> str:
        """IR JSON 落 parse bucket 并注册 StorageObject；返回 storage_object_id。

        key 为内容寻址（SRS §8.1）：同 IR 字节必然同 key，先
        ``find_by_location`` 探测；命中**且对象确实在**（head_exists，SRS
        §8.6：注册行在而对象缺失属完整性事故，不得盲信注册行）才复用；
        对象缺失时重放同内容寻址字节（同 key 同 sha，幂等安全），并刷新
        注册行的校验时间。
        """
        payload = json.dumps(
            doc.to_dict(), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        sha = hashlib.sha256(payload).hexdigest()
        key = build_object_key("parse_ir", sha)
        bucket = self._parse_bucket_name
        location = ObjectLocation(bucket=bucket, object_key=key)

        existing = await self._storage_objects.find_by_location(bucket, key, None)
        if existing is not None and await self._store.head_exists(location):
            await self._storage_objects.mark_verified(existing.id, _utcnow())
            return existing.id  # D-002：同 IR 字节复用对象注册行

        put = await self._store.put_stream(
            location,
            _chunked(payload),
            PutOptions(
                artifact_class="parse_ir",
                mime="application/json",
                expected_sha256=sha,
                metadata={"schema_version": doc.schema_version},
            ),
        )
        if existing is not None:
            # 注册行在但对象曾缺失：字节已重放，刷新校验时间后复用原行。
            await self._storage_objects.mark_verified(existing.id, _utcnow())
            return existing.id
        record = await self._storage_objects.register(
            StorageObjectRecord(
                id=_new_id("so"),
                provider=self._provider(),
                bucket=bucket,
                object_key=key,
                object_version_id=put.version_id,
                sha256=put.sha256,
                size=put.size,
                mime="application/json",
                artifact_class="parse_ir",
                state="AVAILABLE",
                etag=put.etag,
                created_at=_utcnow(),
                last_verified_at=_utcnow(),
            )
        )
        return record.id

    async def _persist_raw_artifact(self, artifact: Any) -> str | None:
        """backend raw artifact JSON 落 parse bucket（整改轮，§9.5 replay 原料）.

        ``BackendParseArtifact.to_dict`` 序列化（契约 v1.2）；key 内容寻址
        （同 artifact 字节同 key）。文本格式的 ``raw_output``（源文本）体积
        可观但同受内容寻址去重保护，保留完整以便升级重放。
        """
        payload = json.dumps(
            artifact.to_dict(), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        sha = hashlib.sha256(payload).hexdigest()
        key = build_object_key("backend_raw", sha)
        bucket = self._parse_bucket_name
        location = ObjectLocation(bucket=bucket, object_key=key)

        existing = await self._storage_objects.find_by_location(bucket, key, None)
        if existing is not None and await self._store.head_exists(location):
            await self._storage_objects.mark_verified(existing.id, _utcnow())
            return existing.id

        put = await self._store.put_stream(
            location,
            _chunked(payload),
            PutOptions(
                artifact_class="backend_raw",
                mime="application/json",
                expected_sha256=sha,
                metadata={"parser_id": artifact.parser_id},
            ),
        )
        if existing is not None:
            await self._storage_objects.mark_verified(existing.id, _utcnow())
            return existing.id
        record = await self._storage_objects.register(
            StorageObjectRecord(
                id=_new_id("so"),
                provider=self._provider(),
                bucket=bucket,
                object_key=key,
                object_version_id=put.version_id,
                sha256=put.sha256,
                size=put.size,
                mime="application/json",
                artifact_class="backend_raw",
                state="AVAILABLE",
                etag=put.etag,
                created_at=_utcnow(),
                last_verified_at=_utcnow(),
            )
        )
        return record.id

    def _provider(self) -> str:
        """从对象存储适配器读 provider 标识（Fake/Minio 均暴露 ``provider``）。

        provider 是溯源/完整性字段，不得猜测（缺属性即配置错误，fail-fast）。
        """
        provider = getattr(self._store, "provider", None)
        if not provider:
            raise ValueError(
                f"object store {type(self._store).__name__} does not expose a "
                "'provider' attribute; cannot register storage object"
            )
        return provider

    # -- 投影行 ---------------------------------------------------------------

    async def _record_success(
        self,
        frozen: FrozenInput,
        run_id: str,
        started_at: str,
        doc: ParsedDocument,
        parse_ir_object_id: str,
        quality_meta: dict[str, Any] | None = None,
    ) -> ParseRunRecord:
        record = ParseRunRecord(
            id=run_id,
            document_id=frozen.document_id,
            source_storage_object_id=frozen.source_storage_object_id,
            source_raw_hash=frozen.source_raw_hash,
            source_content_revision=frozen.source_content_revision,
            parser_id=self._parser.descriptor.parser_id,
            parser_fingerprint=self._parser.descriptor.parser_fingerprint,
            status="SUCCEEDED",
            parse_ir_storage_object_id=parse_ir_object_id,
            parse_ir_schema_version=doc.schema_version,
            element_count=len(doc.elements),
            container_count=len(doc.containers),
            relation_count=len(doc.relations),
            started_at=started_at,
            finished_at=_utcnow(),
            metadata_json=self._run_metadata(doc, quality_meta or {}),
        )
        return await self._parse_runs.upsert(record)

    async def _record_failure(
        self,
        frozen: FrozenInput,
        run_id: str,
        started_at: str,
        exc: Exception,
    ) -> None:
        record = ParseRunRecord(
            id=run_id,
            document_id=frozen.document_id,
            source_storage_object_id=frozen.source_storage_object_id,
            source_raw_hash=frozen.source_raw_hash,
            source_content_revision=frozen.source_content_revision,
            parser_id=self._parser.descriptor.parser_id,
            parser_fingerprint=self._parser.descriptor.parser_fingerprint,
            status="FAILED",
            error_message=f"{type(exc).__name__}: {exc}",
            started_at=started_at,
            finished_at=_utcnow(),
            metadata_json=self._fail_metadata(),
        )
        await self._parse_runs.upsert(record)

    # -- 元数据 ---------------------------------------------------------------

    def _run_metadata(
        self,
        doc: ParsedDocument,
        quality_meta: dict[str, Any] | None = None,
    ) -> str:
        descriptor = self._parser.descriptor
        return json.dumps(
            {
                "mode": "shadow",  # 影子链路标记：不进发布（M2 退出条件）
                "parser_id": descriptor.parser_id,
                "parser_version": descriptor.version,
                "schema_version": doc.schema_version,
                "warnings": list(doc.diagnostics.warnings),
                **(quality_meta or {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _fail_metadata(self) -> str:
        descriptor = self._parser.descriptor
        return json.dumps(
            {
                "mode": "shadow",
                "parser_id": descriptor.parser_id,
                "parser_version": descriptor.version,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


__all__ = ["ShadowParseService"]
