"""RepresentationStore 内存实现（批次8 M2 暂存；M5 接三面资产入库）.

快照级替换语义（与 SegmentStore 对齐）：projector 幂等重跑时整体替换
该快照的表示，不累积重复。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrieRepresentation,
)
from knowledge_mining.mining.retrieval_projection.embedding import EmbeddingRecord


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


class MemoryEmbeddingStore:
    """向量记录暂存（M5 由三面资产入库接管；快照级替换语义）.

    ``vectors`` 仅为与 PgEmbeddingStore 同构的契约参数——memory 实现不落
    向量本体（无消费方），显式忽略。
    """

    def __init__(self) -> None:
        self._by_snapshot: dict[str, tuple[EmbeddingRecord, ...]] = {}
        self._fingerprints: dict[str, str] = {}

    async def replace_for_snapshot(
        self,
        snapshot_id: str,
        records: tuple[EmbeddingRecord, ...],
        policy_version: str,
        *,
        document_key: str,
        vectors: tuple = (),
    ) -> int:
        self._by_snapshot[snapshot_id] = records
        self._fingerprints[snapshot_id] = policy_version
        return len(records)

    async def list_for_snapshot(
        self, snapshot_id: str
    ) -> tuple[EmbeddingRecord, ...]:
        return self._by_snapshot.get(snapshot_id, ())


class MemoryAliasStore:
    """别名（query_alias/summary_alias）暂存；M5 持久化时并入搜索面."""

    def __init__(self) -> None:
        self._by_snapshot: dict[str, tuple] = {}

    async def replace_for_snapshot(
        self, snapshot_id, aliases, fingerprint, *, document_key,
    ) -> int:
        self._by_snapshot[snapshot_id] = tuple(aliases)
        return len(aliases)

    async def list_for_snapshot(self, snapshot_id):
        return self._by_snapshot.get(snapshot_id, ())
