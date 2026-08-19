"""In-memory fake repository for the Segment Compiler layer (M5).

Implements ``SegmentStore``（``contracts/segment_compiler.py``）——快照级
**替换语义**（重切覆盖旧切片与 links，对齐旧链路 db 按快照删除重写的
惯例），供服务测试与本地开发使用（ADR-0003 D-006/D-022）。
"""
from __future__ import annotations

from dataclasses import dataclass

from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentElementLink,
)


@dataclass(frozen=True)
class _Stored:
    snapshot_id: str
    segments: tuple[CompiledSegment, ...]
    links: tuple[SegmentElementLink, ...]
    compiler_fingerprint: str
    document_key: str


class MemorySegmentStore:
    """In-memory ``SegmentStore``（每快照一份存储，重切整体替换）."""

    def __init__(self) -> None:
        self._by_snapshot: dict[str, _Stored] = {}

    async def replace_for_snapshot(
        self,
        snapshot_id: str,
        segments: tuple[CompiledSegment, ...],
        compiler_fingerprint: str,
        *,
        document_key: str,
    ) -> int:
        links = tuple(
            link for seg in segments for link in seg.links
        )
        self._by_snapshot[snapshot_id] = _Stored(
            snapshot_id=snapshot_id, segments=segments, links=links,
            compiler_fingerprint=compiler_fingerprint, document_key=document_key,
        )
        return len(segments)

    async def list_for_snapshot(
        self, snapshot_id: str
    ) -> tuple[CompiledSegment, ...]:
        stored = self._by_snapshot.get(snapshot_id)
        return stored.segments if stored else ()

    # -- 测试辅助（非 Protocol 成员） ---------------------------------------

    def compiler_fingerprint(self, snapshot_id: str) -> str | None:
        stored = self._by_snapshot.get(snapshot_id)
        return stored.compiler_fingerprint if stored else None

    async def link_count(self, snapshot_id: str) -> int:
        stored = self._by_snapshot.get(snapshot_id)
        return len(stored.links) if stored else 0


__all__ = ["MemorySegmentStore"]
