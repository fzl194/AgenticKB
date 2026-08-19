"""M5.4 解析结果只读服务（RED 先行）：文件绑定的结构化数据视图."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.asyncio

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from tests.segment_compiler.test_projection_and_store import (  # noqa: E402
    _doc,
    _seed_ir,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402
from knowledge_mining.mining.segment_compiler.repositories_memory import (  # noqa: E402
    MemorySegmentStore,
)
from knowledge_mining.mining.snapshot_store.repositories_memory import (  # noqa: E402
    MemorySnapshotRepository,
)
from knowledge_mining.mining.snapshot_store.service import (  # noqa: E402
    SnapshotCommitService,
)
from tests.snapshot_store.test_commit_service import _frozen  # noqa: E402


async def test_read_service_returns_structured_view(tmp_path) -> None:
    from knowledge_mining.mining.snapshot_store.read_service import (
        ParseResultReadService,
    )
    from knowledge_mining.mining.segment_compiler.service import (
        SegmentCompileService,
    )
    from tests.snapshot_store.test_commit_service import _decision

    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    ir_id = await _seed_ir(store, objects)
    snapshots = MemorySnapshotRepository()

    async def _no_stale(frozen) -> None:  # noqa: ANN001
        return None

    commit = SnapshotCommitService(
        snapshots=snapshots, stale_checker=_no_stale,
        storage_objects=objects, object_store=store,
    )
    frozen = _frozen()
    committed = await commit.commit(
        frozen=frozen, document=_doc(),
        parse_ir_storage_object_id=ir_id,
        quality_decision=_decision(), run_id="r1", domain="default",
        title="手册",
    )
    seg_store = MemorySegmentStore()
    await SegmentCompileService(
        object_store=store, storage_objects=objects, segment_store=seg_store,
    ).compile(
        committed.snapshot.id, parse_ir_storage_object_id=ir_id,
        document_key="a.pdf",
    )

    read = ParseResultReadService(
        snapshots=snapshots, storage_objects=objects, object_store=store,
        segment_store=seg_store,
    )
    result = await read.get_parse_result(
        domain="default", document_id=frozen.document_id,
    )
    assert result["snapshot"]["id"] == committed.snapshot.id
    assert result["snapshot"]["quality_status"] in ("PASS", "WARN")
    assert result["snapshot"]["source_content_revision"] == 3  # 出生证明
    assert [o["title"] for o in result["outline"]] == ["章一"]
    # 对抗评审 HIGH-2 修复后 elements 为 {count, items} 限界结构。
    types = {e["element_type"] for e in result["elements"]["items"]}
    assert "heading" in types and "paragraph" in types
    assert result["elements"]["count"] >= len(result["elements"]["items"])
    assert result["segments"]["count"] >= 1
    assert result["segments"]["items"][0]["block_type"]


async def test_read_service_missing_document_returns_none(tmp_path) -> None:
    from knowledge_mining.mining.snapshot_store.read_service import (
        ParseResultReadService,
    )

    read = ParseResultReadService(
        snapshots=MemorySnapshotRepository(),
        storage_objects=MemoryStorageObjectRepository(),
        object_store=FakeObjectStore(str(tmp_path / "objects")),
        segment_store=MemorySegmentStore(),
    )
    assert await read.get_parse_result(
        domain="default", document_id="nobody",
    ) is None
