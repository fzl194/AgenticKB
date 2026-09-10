"""A1 来源记录物化服务（38 号 §2.3）.

两条使用路径：

1. **挖掘主链**（retrieval_unit_project_handler 成功后）：读 **staging**
   units（replace 刚写完），物化 → locator staging——晋升由 Build 组装
   事务经 ``PROMOTE_TABLE_COLUMNS`` 统一完成；
2. **受控重放**（replay.py）：读 **final** units（committed 快照），物化 →
   staging → ``promote_locators`` 专用最小晋升。不动 snapshot 指纹、不发
   Build、不失效 ref（37 号 FR-A1-3 硬约束）。

IR 加载带 sha256 完整性校验（同 SegmentCompileService 纪律：注册行损坏/
不匹配按完整性事故抛错，不静默跳过）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument
from knowledge_mining.mining.source_locator.extract import (
    LocatorRecord,
    extract_locator_records,
)


@dataclass(frozen=True)
class MaterializeOutcome:
    """物化结果（handler 计事实 / replay 出审计统计）."""

    snapshot_id: str
    record_count: int
    status: str  # ok | skipped_no_snapshot | skipped_no_ir | skipped_no_units
    detail: str = ""


class SourceLocatorService:
    def __init__(
        self,
        *,
        snapshots: Any,
        storage_objects: Any,
        object_store: Any,
        locator_store: Any,
    ) -> None:
        self._snapshots = snapshots
        self._storage_objects = storage_objects
        self._object_store = object_store
        self._locator_store = locator_store

    async def materialize(
        self,
        snapshot_id: str,
        *,
        representations: Any = None,
    ) -> MaterializeOutcome:
        """物化一个快照的来源记录.

        ``representations``：显式传入（主链 = staging units）；缺省时经
        ``locator_store.list_final_representations`` 读 final（重放路径）。
        """
        from knowledge_mining.mining.snapshot_store.ir_access import (
            SnapshotIRUnavailable,
            load_parsed_document,
        )

        try:
            doc = await load_parsed_document(
                snapshots=self._snapshots,
                storage_objects=self._storage_objects,
                object_store=self._object_store,
                snapshot_id=snapshot_id,
            )
        except SnapshotIRUnavailable as exc:
            status = (
                "skipped_no_snapshot" if exc.reason == "no_snapshot"
                else "skipped_no_ir"
            )
            return MaterializeOutcome(snapshot_id, 0, status, detail=str(exc))

        if representations is None:
            representations = await self._locator_store.list_final_representations(
                snapshot_id
            )
        if not representations:
            return MaterializeOutcome(
                snapshot_id, 0, "skipped_no_units",
            )
        records = extract_locator_records(doc, representations)
        await self._locator_store.replace_for_snapshot(snapshot_id, records)
        return MaterializeOutcome(snapshot_id, len(records), "ok")


class SourceLocatorFacade:
    """工作流同步门面（镜像 RetrieProjectFacade：handler 只认这个形状）.

    主链时机 = retrieval_unit_project 刚把 units 写入 staging 之后——本
    门面从**同一个 representation_store** 读 staging units 传入服务。
    物化失败由 handler 决定 degraded 语义；本门面不吞异常。
    """

    def __init__(self, service: SourceLocatorService, representation_store: Any) -> None:
        self._service = service
        self._representations = representation_store

    def materialize_for_snapshot(self, *, snapshot_id: str | None) -> Any:
        if not snapshot_id:
            return SimpleNamespace(
                snapshot_id=None, record_count=0,
                status="skipped_no_snapshot",
            )
        from knowledge_mining.mining.workflow.new_chain_services import _run_sync

        representations = _run_sync(
            self._representations.list_for_snapshot(snapshot_id)
        )
        return _run_sync(
            self._service.materialize(
                snapshot_id, representations=representations
            )
        )


__all__ = [
    "LocatorRecord",
    "MaterializeOutcome",
    "SourceLocatorFacade",
    "SourceLocatorService",
]
