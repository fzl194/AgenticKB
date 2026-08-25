"""Snapshot Commit service（M4 WP9，SRS §4.10 / §9.4 / §8.3A）.

把一次通过质量门禁（PASS/WARN）的解析执行**转正**为不可变 Document
Snapshot。提交协议（按 §9.4 Snapshot Commit 状态机的顺序执行）：

```text
STAGING_ARTIFACTS   ①IR 制品完整性校验（§8.6：注册行 + 对象字节都在）
                    ②pre-commit revision check（§4.10：文档当前 revision
                      仍等于冻结值；过期 → FrozenInputStale 透传，调用方
                      把 Run 标 SUPERSEDED——本服务不写任何快照行）
COMPILING           ③合成 snapshot_fingerprint（domain + raw hash +
                      effective pipeline fingerprint，§8.3A）与快照/link 记录
READY               ④仓储幂等提交（指纹命中 → 复用既有行）
```

失败语义（§9.4「失败由 Run 记录，不产生半成品知识快照」）：①②任一失败
在写仓储**之前**抛出，快照表零写入。

设计（ADR-0003 D-001 / D-022）：
- 只依赖注入的 Protocol（SnapshotRepository / StorageObjectRepository /
  ObjectStorePort / stale_checker 可调用），不 import 具体 parser；
- ``stale_checker`` 默认无操作——生产编排层注入
  ``FrozenInputService.check_stale``；测试注入可控 stub。
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from knowledge_mining.mining.contracts.file_management import (
    StorageObjectRepository,
)
from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument
from knowledge_mining.mining.contracts.parser_adapter import (
    effective_pipeline_fingerprint,
)
from knowledge_mining.mining.contracts.snapshot_store import (
    SnapshotCommitResult,
    SnapshotRecord,
    SnapshotRepository,
    SnapshotSourceLink,
    snapshot_fingerprint,
)
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import ObjectLocation
from knowledge_mining.mining.frozen_input.contracts import FrozenInput
from knowledge_mining.mining.parse_quality.gate import QualityDecision

logger = logging.getLogger(__name__)

StaleChecker = Callable[[FrozenInput], Awaitable[None]]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class SnapshotCommitService:
    """质量门控的快照转正（WP9；见模块 docstring 的提交协议）."""

    def __init__(
        self,
        *,
        snapshots: SnapshotRepository,
        stale_checker: StaleChecker | None = None,
        storage_objects: StorageObjectRepository | None = None,
        object_store: ObjectStorePort | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._stale_checker = stale_checker
        self._storage_objects = storage_objects
        self._object_store = object_store

    async def commit(
        self,
        *,
        frozen: FrozenInput,
        document: ParsedDocument,
        parse_ir_storage_object_id: str,
        quality_decision: QualityDecision,
        run_id: str,
        domain: str,
        title: str | None = None,
        snapshot_id: str | None = None,
        compiler_fingerprint: str | None = None,
    ) -> SnapshotCommitResult:
        """转正一次质量合格（PASS/WARN）的解析执行.

        - FAIL/REPAIR/FALLBACK 决策直接拒绝（REPAIR/FALLBACK 是编排层的
          继续动作，不是可提交结论）；
        - 任何前置校验失败都在写仓储前抛出（不产生半成品快照）；
        - ``compiler_fingerprint``（A08）：切片策略变化 → 传入新指纹产
          **新快照**（复用 IR，不重新解析）；解析即转正时为 None。
        """
        if quality_decision.decision not in ("PASS", "WARN"):
            raise ValueError(
                f"only PASS/WARN parses may commit a snapshot, got "
                f"{quality_decision.decision!r} (M4 exit criteria: low "
                f"quality never becomes a READY snapshot)"
            )

        # STAGING_ARTIFACTS ①：IR 制品完整性（§8.6——注册行与字节都在）。
        await self._verify_ir_object(parse_ir_storage_object_id)
        # STAGING_ARTIFACTS ②：pre-commit revision check（§4.10）。
        if self._stale_checker is not None:
            await self._stale_checker(frozen)

        # COMPILING ③：指纹与记录合成。
        identity = document.source_identity
        pipeline_fp = effective_pipeline_fingerprint(
            parser_fingerprint=identity.parser_fingerprint,
            normalizer_version=identity.normalizer_version,
            rule_config_fingerprint=identity.rule_config_fingerprint,
            dependency_fingerprint=identity.dependency_fingerprint,
            reconciler_version=identity.reconciler_version,
            parse_ir_schema_version=identity.parse_ir_schema_version,
        )
        fp = snapshot_fingerprint(
            domain=domain,
            source_raw_hash=frozen.source_raw_hash,
            effective_pipeline_fingerprint=pipeline_fp,
            compiler_fingerprint=compiler_fingerprint,
        )
        snapshot = SnapshotRecord(
            id=snapshot_id or _new_id("snap"),
            domain=domain,
            snapshot_fingerprint=fp,
            raw_content_hash=frozen.source_raw_hash,
            normalized_content_hash=frozen.source_raw_hash,
            mime_type=frozen.mime,
            title=title or frozen.original_filename,
            parse_ir_storage_object_id=parse_ir_storage_object_id,
            parse_ir_schema_version=document.schema_version,
            parser_fingerprint=identity.parser_fingerprint,
            compiler_fingerprint=compiler_fingerprint,
            quality_status=quality_decision.decision,
            created_by_run_id=run_id,
            created_at=_utcnow(),
            metadata_json=self._metadata(quality_decision, identity),
        )
        link = SnapshotSourceLink(
            id=_new_id("sl"),
            document_id=frozen.document_id,
            document_snapshot_id=snapshot.id,
            source_storage_object_id=frozen.source_storage_object_id,
            source_content_revision=frozen.source_content_revision,
            title=snapshot.title,
            linked_at=_utcnow(),
            source_uri=(
                f"{frozen.provider}://{frozen.bucket}/{frozen.object_key}"
            ),
            relative_path=frozen.object_key,
        )

        # READY ④：幂等提交。
        result = await self._snapshots.commit(snapshot, link)
        if result.created:
            logger.info(
                "snapshot %s committed (run=%s domain=%s quality=%s)",
                snapshot.id, run_id, domain, quality_decision.decision,
            )
        else:
            logger.info(
                "snapshot commit reused existing %s (run=%s)",
                result.snapshot.id, run_id,
            )
        return result

    async def mark_lifecycle(
        self, snapshot_id: str, lifecycle_status: str
    ) -> SnapshotRecord:
        """READY → DEPRECATED/REVOKED 运维标记（§9.3 不可逆）."""
        return await self._snapshots.mark_lifecycle(snapshot_id, lifecycle_status)

    # -- 内部 ---------------------------------------------------------------

    async def _verify_ir_object(self, storage_object_id: str) -> None:
        """IR 制品完整性校验（§8.6）：注册行在且对象字节确实在.

        未注入依赖时跳过（测试/影子用途），生产编排必须注入两者。
        """
        if self._storage_objects is None or self._object_store is None:
            return
        from knowledge_mining.mining.contracts.storage.errors import (
            StorageObjectMissing,
        )

        record = await self._storage_objects.get(storage_object_id)
        if record is None:
            raise StorageObjectMissing(
                f"parse IR storage object {storage_object_id!r} is not "
                f"registered; refusing to commit a snapshot without its "
                f"artifact (SRS §8.6)"
            )
        location = ObjectLocation(
            bucket=record.bucket, object_key=record.object_key,
            version_id=record.object_version_id,
        )
        if not await self._object_store.head_exists(location):
            raise StorageObjectMissing(
                f"parse IR object bytes are missing at {record.bucket}/"
                f"{record.object_key}; integrity incident — refusing to "
                f"commit (SRS §8.6)"
            )

    def _metadata(
        self, decision: QualityDecision, identity: Any
    ) -> str:
        from knowledge_mining.mining.parse_quality.metrics import (
            quality_metrics_to_dict,
        )

        return json.dumps(
            {
                "mode": "m4-commit",
                "quality_decision": decision.decision,
                "quality_issues": [
                    {"code": i.code, "message": i.message}
                    for i in decision.issues
                ],
                "quality_metrics": (
                    quality_metrics_to_dict(decision.metrics)
                    if decision.metrics is not None else None
                ),
                "normalizer_version": identity.normalizer_version,
                "reconciler_version": identity.reconciler_version,
                "rule_config_fingerprint": identity.rule_config_fingerprint,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


__all__ = ["SnapshotCommitService", "StaleChecker"]
