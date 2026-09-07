"""来源记录内存存储（测试/开发；对齐 retrieval_projection 内存件纪律）."""
from __future__ import annotations

from knowledge_mining.mining.source_locator.extract import LocatorRecord


class MemoryLocatorStore:
    """快照级替换 + 只读列举；final/staging 双段语义由测试显式驱动."""

    def __init__(self) -> None:
        self._staging: dict[str, tuple[LocatorRecord, ...]] = {}
        self._final: dict[str, tuple[LocatorRecord, ...]] = {}

    async def replace_for_snapshot(
        self, snapshot_id: str, records: tuple[LocatorRecord, ...]
    ) -> int:
        self._staging[snapshot_id] = tuple(records)
        return len(records)

    async def list_for_snapshot(self, snapshot_id: str) -> tuple[LocatorRecord, ...]:
        return self._staging.get(snapshot_id, ())

    async def list_final_for_snapshot(
        self, snapshot_id: str
    ) -> tuple[LocatorRecord, ...]:
        return self._final.get(snapshot_id, ())

    async def list_final_representations(self, snapshot_id: str) -> tuple:
        """重放路径专用（final units 读）——内存件不承载 units，显式失败."""
        raise NotImplementedError(
            "MemoryLocatorStore does not serve final representations; "
            "replay requires PgLocatorStore"
        )

    async def promote_locators(self, snapshot_ids: list[str]) -> int:
        """专用最小晋升：只动 locator 双表，绝不动其他六张派生表."""
        for snapshot_id in snapshot_ids:
            self._final[snapshot_id] = self._staging.get(snapshot_id, ())
            self._staging.pop(snapshot_id, None)
        return len(snapshot_ids)
