"""In-memory fake repository for the Snapshot Store layer (M4 WP9).

Implements ``SnapshotRepository``（``contracts/snapshot_store.py``）backed by
plain ``dict`` stores——整套转正链路无需 PostgreSQL 即可跑通（ADR-0003
D-006 / D-022，与 file_management / shadow_parse 的 memory 仓储同风格）。

幂等模型（SRS §2.2 / §8.3A）：
- 唯一键 ``(domain, snapshot_fingerprint)`` 维护二级索引；
- ``commit`` 命中既有指纹 → 返回原行（``created=False``），不重复插入；
- ``mark_lifecycle`` 只允许 READY → DEPRECATED/REVOKED（§9.3 不可逆标记）。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.snapshot_store import (
    SnapshotCommitResult,
    SnapshotRecord,
    SnapshotSourceLink,
)

_FpKey = tuple[str, str]


class MemorySnapshotRepository:
    """In-memory ``SnapshotRepository``（dict 双索引：id + 指纹）."""

    def __init__(self) -> None:
        self._by_id: dict[str, SnapshotRecord] = {}
        self._by_fp: dict[_FpKey, str] = {}
        self._links: dict[str, SnapshotSourceLink] = {}

    async def commit(
        self, snapshot: SnapshotRecord, link: SnapshotSourceLink
    ) -> SnapshotCommitResult:
        fp_key = (snapshot.domain, snapshot.snapshot_fingerprint)
        existing_id = self._by_fp.get(fp_key)
        if existing_id is not None:
            existing = self._by_id[existing_id]
            return SnapshotCommitResult(
                snapshot=existing,
                created=False,
                reused_reason="fingerprint_hit",
            )
        self._by_id[snapshot.id] = snapshot
        self._by_fp[fp_key] = snapshot.id
        self._links[link.id] = link
        return SnapshotCommitResult(snapshot=snapshot, created=True)

    async def get(self, snapshot_id: str) -> SnapshotRecord | None:
        return self._by_id.get(snapshot_id)

    async def find_by_fingerprint(
        self, domain: str, fingerprint: str
    ) -> SnapshotRecord | None:
        rid = self._by_fp.get((domain, fingerprint))
        return self._by_id[rid] if rid else None

    async def latest_for_document(
        self, document_id: str, domain: str
    ) -> tuple[SnapshotRecord, SnapshotSourceLink] | None:
        candidates = [
            (snap, link) for link in self._links.values()
            if link.document_id == document_id
            for snap in [self._by_id.get(link.document_snapshot_id)]
            if snap is not None
            and snap.domain == domain
            and snap.lifecycle_status == "READY"
            and snap.snapshot_fingerprint
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda pair: pair[0].created_at)

    async def mark_lifecycle(
        self, snapshot_id: str, lifecycle_status: str
    ) -> SnapshotRecord:
        from dataclasses import replace

        existing = self._by_id.get(snapshot_id)
        if existing is None:
            raise KeyError(f"unknown snapshot id: {snapshot_id!r}")
        if existing.lifecycle_status != "READY":
            raise ValueError(
                f"lifecycle is one-way from READY; current="
                f"{existing.lifecycle_status!r}, requested={lifecycle_status!r}"
            )
        if lifecycle_status not in ("DEPRECATED", "REVOKED"):
            raise ValueError(
                f"lifecycle may only move to DEPRECATED/REVOKED, got "
                f"{lifecycle_status!r}"
            )
        updated = replace(existing, lifecycle_status=lifecycle_status)
        self._by_id[snapshot_id] = updated
        return updated

    # -- 测试辅助（非 Protocol 成员） ---------------------------------------

    def count(self) -> int:
        """已提交快照行数。"""
        return len(self._by_id)

    def links(self) -> tuple[SnapshotSourceLink, ...]:
        return tuple(self._links.values())


__all__ = ["MemorySnapshotRepository"]
