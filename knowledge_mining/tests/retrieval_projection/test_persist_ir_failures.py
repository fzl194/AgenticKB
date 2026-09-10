"""P1-10 回归：persist 不得吞 IR 数据损坏（Codex 审查）.

缺陷：``_persist_for_snapshot_once`` 对 IR 加载 ``except Exception``
一律降级 ``table_facts=None`` 继续发布——SHA-256 错配、对象缺失、
JSON 损坏等**数据损坏/瞬时故障**被永久固化为"缺类型化事实的正式
Build"（看似成功实则残缺）。

契约：
- ``SnapshotIRUnavailable``（无对象注册/缺 object_id——按设计的降级）
  → ``table_facts=None`` 继续；
- ``StorageObjectCorrupt`` / ``StorageObjectMissing`` / JSON 解析错误 /
  对象存储读取失败 → **向上抛出**（触发文档失败/重试），不发布。
"""
from __future__ import annotations

from typing import Any

import pytest


class _ListStore:
    def __init__(self, rows: tuple = ()) -> None:
        self._rows = rows

    async def list_for_snapshot(self, _snapshot_id: str) -> tuple:
        return self._rows


class _FailingIrLoader:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __call__(self, _snapshot_id: str) -> Any:
        raise self._exc


def _service(ir_loader: Any) -> Any:
    from knowledge_mining.mining.retrieval_projection.persist import (
        AssetPersistService,
        MemoryAssetWriter,
    )

    return AssetPersistService(
        segment_store=_ListStore(),
        representation_store=_ListStore(),
        embedding_store=_ListStore(),
        writer=MemoryAssetWriter(),
        ir_loader=ir_loader,
    )


@pytest.mark.asyncio
async def test_corrupt_sha_propagates():
    from knowledge_mining.mining.contracts.storage.errors import (
        StorageObjectCorrupt,
    )

    with pytest.raises(StorageObjectCorrupt):
        _service(_FailingIrLoader(StorageObjectCorrupt("sha mismatch"))). \
            persist_for_snapshot(snapshot_id="s1", document_ref="d.md")


@pytest.mark.asyncio
async def test_missing_storage_object_propagates():
    from knowledge_mining.mining.contracts.storage.errors import (
        StorageObjectMissing,
    )

    with pytest.raises(StorageObjectMissing):
        _service(_FailingIrLoader(StorageObjectMissing("gone"))). \
            persist_for_snapshot(snapshot_id="s1", document_ref="d.md")


@pytest.mark.asyncio
async def test_invalid_json_propagates():
    with pytest.raises(ValueError):
        _service(_FailingIrLoader(ValueError("bad json"))). \
            persist_for_snapshot(snapshot_id="s1", document_ref="d.md")


@pytest.mark.asyncio
async def test_object_store_read_failure_propagates():
    from knowledge_mining.mining.table_assets.facts import (
        extract_table_facts,
    )

    # IR 加载成功但事实抽取抛错（对象存储读块失败在 load 内已抛——
    # 这里覆盖 extract 阶段的意外异常同样不得静默）
    class _BadExtract:
        async def __call__(self, _snapshot_id: str) -> Any:
            return object()  # 非 ParsedDocument：extract 会 TypeError

    with pytest.raises(Exception):
        _service(_BadExtract()).persist_for_snapshot(
            snapshot_id="s1", document_ref="d.md",
        )


@pytest.mark.asyncio
async def test_ir_unavailable_still_degrades_by_design():
    from knowledge_mining.mining.snapshot_store.ir_access import (
        SnapshotIRUnavailable,
    )

    outcome = _service(
        _FailingIrLoader(SnapshotIRUnavailable("no ir", reason="no_ir")),
    ).persist_for_snapshot(snapshot_id="s1", document_ref="d.md")
    assert outcome is not None  # 降级发布（table_facts=None），不抛
