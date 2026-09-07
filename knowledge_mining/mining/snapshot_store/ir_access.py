"""快照 Parse IR 加载（A3 抽取的共享通道）.

此前 40 行加载逻辑在 SourceLocatorService 与 SegmentCompileService 重复
（A1 审查遗留项）——A3 的表格事实回填是第三个消费方，先收口到这里。
内容寻址校验（注册 sha256 vs 实际字节）在加载层一次完成。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument
from knowledge_mining.mining.contracts.storage.errors import (
    StorageObjectCorrupt,
    StorageObjectMissing,
)
from knowledge_mining.mining.contracts.storage.types import ObjectLocation


class SnapshotIRUnavailable(Exception):
    """快照 IR 不可用（无对象注册/缺 object_id）——调用方按降级处理."""

    def __init__(self, message: str, *, reason: str = "no_snapshot") -> None:
        super().__init__(message)
        self.reason = reason


async def load_parsed_document(
    *,
    snapshots: Any,
    storage_objects: Any,
    object_store: Any,
    snapshot_id: str,
) -> ParsedDocument:
    """按快照 id 加载并校验 Parse IR（sha256 内容寻址）.

    - 快照不存在 / 无 parse_ir_storage_object_id → :class:`SnapshotIRUnavailable`
      （调用方降级，不是数据损坏）；
    - 对象缺失 / sha256 不符 → 存储异常上抛（数据完整性问题必须可见）。
    """
    snapshot = await snapshots.get(snapshot_id)
    if snapshot is None:
        raise SnapshotIRUnavailable(f"snapshot {snapshot_id!r} not found")
    object_id = getattr(snapshot, "parse_ir_storage_object_id", None)
    if not object_id:
        raise SnapshotIRUnavailable(
            f"snapshot {snapshot_id!r} has no parse IR object",
            reason="no_ir",
        )
    record = await storage_objects.get(object_id)
    if record is None:
        raise StorageObjectMissing(
            f"parse IR storage object {object_id!r} is not registered"
        )
    location = ObjectLocation(
        bucket=record.bucket,
        object_key=record.object_key,
        version_id=record.object_version_id,
    )
    chunks: list[bytes] = []
    async for chunk in object_store.get_stream(location):
        chunks.append(chunk)
    payload = b"".join(chunks)
    recorded = record.sha256
    if not (isinstance(recorded, str) and len(recorded) == 64):
        raise StorageObjectCorrupt(
            f"parse IR object {object_id!r} has invalid registered sha256"
        )
    if recorded != hashlib.sha256(payload).hexdigest():
        raise StorageObjectCorrupt(
            f"parse IR object {object_id!r} sha256 mismatch"
        )
    return ParsedDocument.from_dict(json.loads(payload))


__all__ = ["SnapshotIRUnavailable", "load_parsed_document"]
