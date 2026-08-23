"""Segment Compile service（M5，SRS §4.12 / §C11 / A08）.

从知识快照的 Parse IR 对象**编译**切片并落库：

```text
snapshot 的 parse_ir 对象（MinIO/Fake）
  -> 读回 IR JSON -> ParsedDocument.from_dict
  -> compile_segments(doc, policy)          # 不重读原文件、不重新解析
  -> SegmentStore.replace_for_snapshot(...)  # 快照级替换（重切覆盖）
```

A08 语义：切片策略（``SegmentPolicy``）或编译器版本变化 →
``compiler_fingerprint`` 变化 → 调用方（M6 workflow / M5.4 重切入口）
产生**新快照**时复用同一 IR 对象——本服务只负责"给定 IR 和策略，产出
并存储切片"，不做快照生命周期管理。

设计（ADR-0003 D-022）：只依赖注入 Protocol（ObjectStorePort /
StorageObjectRepository / SegmentStore），不 import 具体 adapter。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from knowledge_mining.mining.contracts.file_management import (
    StorageObjectRepository,
)
from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument
from knowledge_mining.mining.contracts.segment_compiler import (
    COMPILER_VERSION,
    CompiledSegment,
    SegmentPolicy,
    compiler_fingerprint,
)
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import ObjectLocation
from knowledge_mining.mining.segment_compiler.compiler import compile_segments


@dataclass(frozen=True)
class SegmentCompileResult:
    """一次编译的摘要（供编排/审计）."""

    snapshot_id: str
    segment_count: int
    compiler_fingerprint: str


@runtime_checkable
class SegmentStore(Protocol):
    """切片落库 Protocol（memory / PG 双实现；快照级替换语义）."""

    async def replace_for_snapshot(
        self,
        snapshot_id: str,
        segments: tuple[CompiledSegment, ...],
        compiler_fingerprint: str,
        *,
        document_key: str,
    ) -> int:
        """替换该快照的全部切片与 element links，返回切片数."""
        ...

    async def list_for_snapshot(
        self, snapshot_id: str
    ) -> tuple[CompiledSegment, ...]:
        """按 segment_index 升序列出该快照的全部切片."""
        ...


class SegmentCompileService:
    """快照 IR + 策略 -> 切片落库（§4.12：读取 element graph，不读原文件）."""

    def __init__(
        self,
        *,
        object_store: ObjectStorePort,
        storage_objects: StorageObjectRepository,
        segment_store: SegmentStore,
    ) -> None:
        self._store = object_store
        self._storage_objects = storage_objects
        self._segments = segment_store

    async def compile(
        self,
        snapshot_id: str,
        *,
        parse_ir_storage_object_id: str,
        document_key: str,
        policy: SegmentPolicy | None = None,
    ) -> SegmentCompileResult:
        policy = policy or SegmentPolicy()
        doc = await self._load_ir(parse_ir_storage_object_id)
        segments = await _compile_offthread(doc, policy)
        fp = compiler_fingerprint(policy)
        existing_fp = None
        fingerprint_reader = getattr(self._segments, "compiler_fingerprint", None)
        if callable(fingerprint_reader):
            existing_fp = await fingerprint_reader(snapshot_id)
        if existing_fp == fp:
            # 同内容多文档共享快照：已有同指纹切片（另一文档刚编译完）——
            # 复用，避免并发 replace 在唯一键上相撞。
            return SegmentCompileResult(
                snapshot_id=snapshot_id, segment_count=len(segments),
                compiler_fingerprint=fp,
            )
        count = await self._segments.replace_for_snapshot(
            snapshot_id, segments, fp, document_key=document_key,
        )
        return SegmentCompileResult(
            snapshot_id=snapshot_id, segment_count=count, compiler_fingerprint=fp,
        )

    # -- 内部 ---------------------------------------------------------------

    async def _load_ir(self, storage_object_id: str) -> ParsedDocument:
        from knowledge_mining.mining.contracts.storage.errors import (
            StorageObjectMissing,
        )

        record = await self._storage_objects.get(storage_object_id)
        if record is None:
            raise StorageObjectMissing(
                f"parse IR storage object {storage_object_id!r} is not registered"
            )
        location = ObjectLocation(
            bucket=record.bucket, object_key=record.object_key,
            version_id=record.object_version_id,
        )
        chunks: list[bytes] = []
        async for chunk in self._store.get_stream(location):
            chunks.append(chunk)
        payload = b"".join(chunks)
        # 对抗评审 MEDIUM-6：注册行的 sha256 非法（非 64 hex）本身即记录
        # 损坏——按完整性事故处理，不得静默跳过校验。
        recorded = record.sha256
        if not (isinstance(recorded, str) and len(recorded) == 64):
            from knowledge_mining.mining.contracts.storage.errors import (
                StorageObjectCorrupt,
            )

            raise StorageObjectCorrupt(
                f"parse IR object {storage_object_id!r} has invalid "
                f"registered sha256; registry row corrupted"
            )
        if recorded != _sha256(payload):
            from knowledge_mining.mining.contracts.storage.errors import (
                StorageObjectCorrupt,
            )

            raise StorageObjectCorrupt(
                f"parse IR object {storage_object_id!r} sha256 mismatch "
                f"(SRS §8.6 integrity incident)"
            )
        return ParsedDocument.from_dict(json.loads(payload))


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def hashlib_hex(value: Any) -> str | None:
    return value if isinstance(value, str) and len(value) == 64 else None


async def _compile_offthread(
    doc: ParsedDocument, policy: SegmentPolicy
) -> tuple[CompiledSegment, ...]:
    import asyncio

    return await asyncio.to_thread(compile_segments, doc, policy)


class SnapshotRecompileService:
    """A08 重切服务（SRS §4.12：切片策略升级 → 复用 IR 产新快照）.

    流程：旧快照 → 读回其 Parse IR（不重新解析/OCR/云调用）→ 以新
    compiler_fingerprint 提交**新快照**（旧快照原样保留，历史可追溯）→
    在新快照下编译切片。质量结论沿用旧快照（解析产物未变），附加
    ``recompiled_from`` 可见性 issue。
    """

    def __init__(
        self,
        *,
        snapshots: Any,
        commit_service: Any,
        compile_service: "SegmentCompileService",
    ) -> None:
        self._snapshots = snapshots
        self._commit = commit_service
        self._compiler = compile_service

    async def recompile(
        self,
        source_snapshot_id: str,
        *,
        frozen: Any,
        domain: str,
        policy: SegmentPolicy | None = None,
        title: str | None = None,
    ) -> tuple[Any, SegmentCompileResult]:
        from knowledge_mining.mining.parse_quality.gate import (
            QualityDecision,
            QualityIssue,
        )

        policy = policy or SegmentPolicy()
        old = await self._snapshots.get(source_snapshot_id)
        if old is None:
            raise KeyError(f"unknown snapshot id: {source_snapshot_id!r}")
        if old.quality_status not in ("PASS", "WARN"):
            # 对抗评审 MEDIUM-6：FAIL 源快照防御性拒绝（正常不可能入库）。
            raise ValueError(
                f"source snapshot {source_snapshot_id!r} has illegal "
                f"quality_status {old.quality_status!r}; refusing recompile"
            )
        document = await self._compiler._load_ir(old.parse_ir_storage_object_id)
        fp = compiler_fingerprint(policy)
        committed = await self._commit.commit(
            frozen=frozen,
            document=document,
            parse_ir_storage_object_id=old.parse_ir_storage_object_id,
            quality_decision=QualityDecision(
                decision=old.quality_status,
                issues=(QualityIssue(
                    code="recompiled_from",
                    message=(
                        f"segments recompiled from snapshot "
                        f"{source_snapshot_id!r} with policy {fp!r}; parse "
                        f"IR reused (A08)"
                    ),
                ),),
            ),
            run_id=f"recompile-{source_snapshot_id[:12]}",
            domain=domain,
            title=title or old.title,
            compiler_fingerprint=fp,
        )
        compiled = await self._compiler.compile(
            committed.snapshot.id,
            parse_ir_storage_object_id=old.parse_ir_storage_object_id,
            document_key=frozen.original_filename or committed.snapshot.id,
            policy=policy,
        )
        return committed.snapshot, compiled


__all__ = [
    "COMPILER_VERSION",
    "SegmentCompileResult",
    "SegmentCompileService",
    "SegmentStore",
    "SnapshotRecompileService",
]
