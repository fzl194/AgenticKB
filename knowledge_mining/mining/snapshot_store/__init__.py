"""Snapshot Store layer (M4 WP9, SRS §3.3 / §4.10 / §9.4 / §8.3A).

质量门控的快照转正链路：PASS/WARN 的解析执行 → pre-commit revision
check → 幂等提交为不可变 Document Snapshot（含来源 link 与完整指纹）。
写入边界：只写 ``asset_document_snapshots`` / ``asset_document_snapshot_links``；
绝不写 ``asset_raw_segments`` / ``mining_run_documents`` / build 选择表
（M5/M6 的职责）。
"""
from knowledge_mining.mining.snapshot_store.repositories_memory import (
    MemorySnapshotRepository,
)
from knowledge_mining.mining.snapshot_store.service import SnapshotCommitService

__all__ = [
    "MemorySnapshotRepository",
    "SnapshotCommitService",
]
