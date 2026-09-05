"""M5.4 解析结果只读服务（RED 先行）：文件绑定的结构化数据视图."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from knowledge_mining.mining.contracts.parse_ir.enums import (  # noqa: E402
    PARSE_IR_SCHEMA_VERSION,
)
from knowledge_mining.mining.contracts.parse_ir.types import (  # noqa: E402
    Element,
    ParseIdentity,
    ParsedDocument,
    TableAsset,
    TableCell,
)
from knowledge_mining.mining.contracts.segment_compiler import (  # noqa: E402
    CompiledSegment,
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
    assert result["outline"][0]["order_index"] == 0
    # 对抗评审 HIGH-2 修复后 elements 为 {count, items} 限界结构。
    types = {e["element_type"] for e in result["elements"]["items"]}
    assert "heading" in types and "paragraph" in types
    assert result["elements"]["count"] >= len(result["elements"]["items"])
    assert result["segments"]["count"] >= 1
    assert result["segments"]["items"][0]["block_type"]
    assert result["segments"]["items"][0]["section_element_id"] == "h0"
    assert result["segments"]["items"][0]["source_order_start"] == 0
    assert result["segments"]["items"][0]["source_order_end"] == 2
    assert result["segments"]["items"][0]["table_ref"] is None
    assert result["segments"]["items"][0]["table_caption"] is None


class _Snapshots:
    def __init__(self) -> None:
        self.snapshot = SimpleNamespace(
            id="snapshot-1",
            parse_ir_storage_object_id="ir-1",
            title="结构测试",
            mime_type="application/pdf",
            quality_status="PASS",
            lifecycle_status="ACTIVE",
            parser_fingerprint="parser@1",
            compiler_fingerprint="compiler@1",
            snapshot_fingerprint="snapshot-fingerprint",
            created_by_run_id="run-1",
            created_at="2026-09-05T00:00:00Z",
        )
        self.link = SimpleNamespace(
            source_storage_object_id="source-1",
            source_content_revision=1,
        )

    async def latest_for_document(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return self.snapshot, self.link

    async def get(self, snapshot_id: str):
        return self.snapshot if snapshot_id == self.snapshot.id else None


class _Segments:
    def __init__(self, segments: tuple[CompiledSegment, ...]) -> None:
        self._segments = segments

    async def list_for_snapshot(self, snapshot_id: str):
        assert snapshot_id == "snapshot-1"
        return self._segments


def _hierarchy_doc() -> ParsedDocument:
    cells = tuple(
        TableCell(
            row_index=row,
            column_index=0,
            text="字段" if row == 0 else f"值-{row}",
            is_header=row == 0,
        )
        for row in range(52)
    )
    # 元组顺序刻意打乱；归属必须按 order_index，而不是数组位置或标题文字。
    elements = (
        Element("p-after", "paragraph", 60, text="尾部", parent_id="h-next"),
        Element(
            "h-child", "heading", 30, text="重复标题",
            parent_id="h-root", style={"level": 2},
        ),
        Element("p-before", "paragraph", 0, text="无标题正文"),
        Element("table-element", "table", 40, text="字段\n值", parent_id="h-child"),
        Element("h-root", "heading", 10, text="重复标题", style={"level": 1}),
        Element("p-root", "paragraph", 20, text="一级正文", parent_id="h-root"),
        Element("h-next", "heading", 50, text="重复标题", style={"level": 1}),
    )
    return ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=ParseIdentity(
            source_raw_hash="raw",
            parser_fingerprint="parser@1",
            parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
        ),
        containers=(),
        elements=elements,
        structured_assets={
            "table-element-table": TableAsset(
                table_id="table-ref-1", page_span_ids=(), rows=52, columns=1,
                cells=cells,
            ),
            "orphan-table": TableAsset(
                table_id="orphan-ref", page_span_ids=(), rows=1, columns=1,
                cells=(TableCell(0, 0, text="无上下文"),),
            ),
        },
    )


async def _read_custom(
    doc: ParsedDocument,
    segments: tuple[CompiledSegment, ...],
):
    from knowledge_mining.mining.snapshot_store.read_service import (
        ParseResultReadService,
    )

    read = ParseResultReadService(
        snapshots=_Snapshots(), storage_objects=None, object_store=None,
        segment_store=_Segments(segments),
    )
    read._load_ir = AsyncMock(return_value=doc)  # type: ignore[method-assign]
    return await read.get_parse_result(domain="default", document_id="doc-1")


async def test_read_service_projects_segment_source_order_and_explicit_table_context() -> None:
    segments = (
        CompiledSegment(
            0, "paragraph", "无标题正文", element_ids=("p-before",),
        ),
        CompiledSegment(
            1, "paragraph", "重复标题\n一级正文",
            element_ids=("h-root", "p-root"),
        ),
        CompiledSegment(
            2, "table", "字段\n值", element_ids=("table-element",),
            metadata={
                "table_ref": "table-ref-1",
                "table_caption": "容量明细",
            },
        ),
        CompiledSegment(
            3, "paragraph", "尾部", element_ids=("p-after",),
        ),
        CompiledSegment(
            4, "paragraph", "失联来源", element_ids=("missing-element",),
            metadata={"table_ref": "missing-table"},
        ),
    )

    result = await _read_custom(_hierarchy_doc(), segments)
    items = result["segments"]["items"]

    assert items[0]["section_element_id"] is None
    assert (items[0]["source_order_start"], items[0]["source_order_end"]) == (0, 0)
    assert items[1]["section_element_id"] == "h-root"
    assert (items[1]["source_order_start"], items[1]["source_order_end"]) == (10, 20)
    assert items[2]["section_element_id"] == "h-child"
    assert (items[2]["source_order_start"], items[2]["source_order_end"]) == (40, 40)
    assert items[2]["table_ref"] == "table-ref-1"
    assert items[2]["table_caption"] == "容量明细"
    assert items[3]["section_element_id"] == "h-next"
    assert items[4]["section_element_id"] is None
    assert items[4]["source_order_start"] is None
    assert items[4]["source_order_end"] is None

    table = next(t for t in result["tables"] if t["table_id"] == "table-ref-1")
    assert table["source_element_id"] == "table-element"
    assert table["parent_section_element_id"] == "h-child"
    assert table["caption"] == "容量明细"
    assert table["preview_truncated"] is True


async def test_table_summary_does_not_guess_context_from_titles() -> None:
    segments = (
        CompiledSegment(
            0, "paragraph", "重复标题", element_ids=("h-root", "p-root"),
        ),
    )

    result = await _read_custom(_hierarchy_doc(), segments)
    orphan = next(t for t in result["tables"] if t["table_id"] == "orphan-ref")

    assert orphan["source_element_id"] is None
    assert orphan["parent_section_element_id"] is None
    assert orphan["caption"] is None
    assert orphan["preview_truncated"] is False


async def test_structure_response_caps_metadata_and_top_level_collections() -> None:
    base = _hierarchy_doc()
    headings = tuple(
        Element(
            f"heading-{index}", "heading", index, text=f"章节 {index}",
            style={"level": 1},
        )
        for index in range(5001)
    )
    tables = {
        f"table-{index}": TableAsset(
            table_id=f"table-{index}", page_span_ids=(), rows=1, columns=1,
            cells=(TableCell(0, 0, text="值"),),
        )
        for index in range(101)
    }
    doc = ParsedDocument(
        schema_version=base.schema_version,
        source_identity=base.source_identity,
        containers=(), elements=headings, structured_assets=tables,
    )
    long_ref = "r" * 1000
    long_caption = "说明" * 500
    result = await _read_custom(
        doc,
        (CompiledSegment(
            0, "table", "值", element_ids=("heading-0",),
            metadata={"table_ref": long_ref, "table_caption": long_caption},
        ),),
    )

    assert len(result["outline"]) == 500
    assert len(result["tables"]) == 100
    assert result["diagnostics"]["outline_total"] == 5001
    assert result["diagnostics"]["tables_total"] == 101
    assert result["diagnostics"]["outline_truncated"] is True
    assert result["diagnostics"]["tables_truncated"] is True
    segment = result["segments"]["items"][0]
    assert len(segment["table_ref"]) == 256
    assert len(segment["table_caption"]) == 240


async def test_title_element_is_a_real_section_boundary() -> None:
    doc = _hierarchy_doc()
    title = Element("title-root", "title", 5, text="文档标题", style={"level": 1})
    paragraph = Element(
        "title-body", "paragraph", 6, text="标题正文", parent_id="title-root",
    )
    titled = ParsedDocument(
        schema_version=doc.schema_version,
        source_identity=doc.source_identity,
        containers=doc.containers,
        elements=(title, paragraph),
        structured_assets={},
    )
    result = await _read_custom(
        titled,
        (CompiledSegment(
            0, "paragraph", "标题正文", element_ids=("title-body",),
        ),),
    )

    assert result["segments"]["items"][0]["section_element_id"] == "title-root"


async def test_explicit_parent_wins_over_active_heading_stack() -> None:
    result = await _read_custom(
        _hierarchy_doc(),
        (CompiledSegment(
            0, "paragraph", "显式归属一级标题",
            heading_chain=((2, "重复标题"),),
            element_ids=("p-root",),
        ),),
    )

    assert result["segments"]["items"][0]["section_element_id"] == "h-root"
    outline = result["outline"]
    assert [item["order_index"] for item in outline] == [10, 30, 50]
    assert outline[1]["parent_section_element_id"] == "h-root"


async def test_empty_segment_heading_chain_does_not_inherit_previous_page_heading() -> None:
    base = _hierarchy_doc()
    doc = ParsedDocument(
        schema_version=base.schema_version,
        source_identity=base.source_identity,
        containers=base.containers,
        elements=(
            Element("page-heading", "heading", 1, text="上一页", style={"level": 1}),
            Element("new-page-body", "paragraph", 2, text="新页无标题正文"),
        ),
        structured_assets={},
    )
    result = await _read_custom(
        doc,
        (CompiledSegment(
            0, "paragraph", "新页无标题正文",
            heading_chain=(), element_ids=("new-page-body",),
        ),),
    )

    assert result["segments"]["items"][0]["section_element_id"] is None


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
