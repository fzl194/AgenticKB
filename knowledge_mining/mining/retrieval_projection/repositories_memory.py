"""RepresentationStore 内存实现（批次8 M2 暂存；M5 接三面资产入库）.

快照级替换语义（与 SegmentStore 对齐）：projector 幂等重跑时整体替换
该快照的表示，不累积重复。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrieRepresentation,
)


class MemoryRepresentationStore:
    def __init__(self) -> None:
        self._by_snapshot: dict[str, tuple[RetrieRepresentation, ...]] = {}
        self._fingerprints: dict[str, str] = {}

    async def replace_for_snapshot(
        self,
        snapshot_id: str,
        representations: tuple[RetrieRepresentation, ...],
        projector_fingerprint: str,
        *,
        document_key: str,
    ) -> int:
        self._by_snapshot[snapshot_id] = representations
        self._fingerprints[snapshot_id] = projector_fingerprint
        return len(representations)

    async def list_for_snapshot(
        self, snapshot_id: str
    ) -> tuple[RetrieRepresentation, ...]:
        return self._by_snapshot.get(snapshot_id, ())

    def projector_fingerprint(self, snapshot_id: str) -> str | None:
        return self._fingerprints.get(snapshot_id)
