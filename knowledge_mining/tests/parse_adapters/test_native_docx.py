"""``NativeDocxParser`` + ``DocxNormalizer`` 单元测试（M3, SRS §C06/§C07/§4.6-§4.7）.

fixture 在测试内用 python-docx 生成 bytes（不依赖外部文件）。覆盖：
heading 层级树（style API 判级）、paragraph_index 证据、合并单元格
（gridSpan 横向 / vMerge 纵向）、TableAsset 网格、单一 section 容器、
parent_of / next_in_reading_order 关系、stable id、round-trip validate、
不支持 MIME / 损坏 bytes 的错误归一。
"""
from __future__ import annotations

import io

import pytest

from knowledge_mining.mining.contracts.parse_ir import validate
from knowledge_mining.mining.contracts.parser_adapter import (
    ParserAdapterError,
    UnsupportedFormat,
)
from knowledge_mining.mining.parse_adapters.native.native_docx import (
    NATIVE_DOCX_FINGERPRINT,
    DocxNormalizer,
    NativeDocxParser,
)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
RAW_HASH = "cd" * 32


def _build_docx_bytes() -> bytes:
    """标题树 + 段落 + 3x3 合并单元格表格的 DOCX bytes."""
    from docx import Document

    doc = Document()
    doc.add_heading("一级标题", level=1)
    doc.add_paragraph("导语段落。")
    doc.add_heading("二级标题", level=2)
    doc.add_paragraph("二级下正文。")
    doc.add_heading("三级标题", level=3)
    doc.add_paragraph("三级下正文。")

    table = doc.add_table(rows=3, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1))  # gridSpan=2
    table.cell(0, 0).text = "表头A"
    table.cell(0, 2).text = "表头C"
    table.cell(1, 0).merge(table.cell(2, 0))  # vMerge restart/continue
    table.cell(1, 0).text = "竖排"
    table.cell(1, 1).text = "x"
    table.cell(1, 2).text = "y"
    table.cell(2, 1).text = "z"
    table.cell(2, 2).text = "w"

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def docx_bytes() -> bytes:
    return _build_docx_bytes()


@pytest.fixture
def parser() -> NativeDocxParser:
    return NativeDocxParser()


@pytest.fixture
def normalizer() -> DocxNormalizer:
    return DocxNormalizer()


def _parse_doc(parser: NativeDocxParser, data: bytes):
    return parser.parse(data, mime=DOCX_MIME)


def _normalize(parser: NativeDocxParser, normalizer: DocxNormalizer, data: bytes):
    artifact = _parse_doc(parser, data)
    return normalizer.normalize(artifact, source_raw_hash=RAW_HASH)


# ---------------------------------------------------------------------------
# RED 1: parse 层 —— 块类型序列、heading level、paragraph_index 证据
# ---------------------------------------------------------------------------

def test_parse_blocks_types_levels_and_native_refs(parser, docx_bytes) -> None:
    artifact = _parse_doc(parser, docx_bytes)

    assert artifact.parser_id == "native_docx"
    assert artifact.mime == DOCX_MIME
    types = [b.block_type for b in artifact.blocks]
    assert types == [
        "heading", "paragraph", "heading", "paragraph",
        "heading", "paragraph", "table",
    ]
    levels = [b.level for b in artifact.blocks if b.block_type == "heading"]
    assert levels == [1, 2, 3]
    # paragraph_index 连续递增（含空段也不会破坏索引语义）
    para_refs = [
        b.native_ref["paragraph_index"]
        for b in artifact.blocks if b.block_type == "paragraph"
    ]
    assert para_refs == [1, 3, 5]
    table_blocks = [b for b in artifact.blocks if b.block_type == "table"]
    assert len(table_blocks) == 1
    assert table_blocks[0].native_ref == {"table_index": 0}
    # DOCX 无页/坐标概念：bbox 不伪造
    assert all(b.bbox is None for b in artifact.blocks)


def test_parse_unsupported_mime_raises(parser) -> None:
    with pytest.raises(UnsupportedFormat):
        parser.parse(b"whatever", mime="application/pdf")


def test_parse_corrupt_bytes_wrapped(parser) -> None:
    with pytest.raises(ParserAdapterError):
        parser.parse(b"not a zip at all", mime=DOCX_MIME)


# ---------------------------------------------------------------------------
# RED 2: IR 断言链 —— parse -> normalize -> validate -> 结构断言
# ---------------------------------------------------------------------------

def test_full_ir_chain_containers_relations_and_parents(
    parser, normalizer, docx_bytes
) -> None:
    doc = _normalize(parser, normalizer, docx_bytes)

    result = validate(doc)
    assert result.valid, [i for i in result.issues if i.level == "error"]

    # 单一 section 容器，无页码伪造
    assert len(doc.containers) == 1
    section = doc.containers[0]
    assert section.container_type == "section"
    assert section.page_number is None

    assert len(doc.elements) == 7
    by_type = {}
    for el in doc.elements:
        by_type.setdefault(el.element_type, []).append(el)
    assert [e.text for e in by_type["heading"]] == ["一级标题", "二级标题", "三级标题"]
    assert [e.text for e in by_type["paragraph"]] == [
        "导语段落。", "二级下正文。", "三级下正文。",
    ]

    h1, h2, h3 = by_type["heading"]
    assert h1.parent_id is None
    assert h2.parent_id == h1.element_id
    assert h3.parent_id == h2.element_id
    assert by_type["paragraph"][0].parent_id == h1.element_id
    assert by_type["paragraph"][1].parent_id == h2.element_id
    assert by_type["paragraph"][2].parent_id == h3.element_id

    parent_of = [
        r for r in doc.relations if r.relation_type == "parent_of"
    ]
    assert len(parent_of) == 6  # h2/h3 + 3 段落 + 表格
    reading = [
        r for r in doc.relations if r.relation_type == "next_in_reading_order"
    ]
    assert len(reading) == 6  # 7 元素 -> 6 条链接

    # fingerprint
    assert doc.source_identity.parser_fingerprint == NATIVE_DOCX_FINGERPRINT
    assert doc.source_identity.parser_fingerprint == (
        "native_docx@1.0.0#python-docx-1.2.0"
    )


def test_table_asset_merges_and_header(parser, normalizer, docx_bytes) -> None:
    doc = _normalize(parser, normalizer, docx_bytes)

    assert list(doc.structured_assets) == [f"{doc.elements[-1].element_id}-table"]
    asset = next(iter(doc.structured_assets.values()))
    assert asset.rows == 3
    assert asset.columns == 3
    grid = {(c.row_index, c.column_index): c for c in asset.cells}

    # 横向合并：gridSpan=2
    assert grid[(0, 0)].text == "表头A"
    assert grid[(0, 0)].column_span == 2
    assert grid[(0, 0)].row_span == 1
    assert (0, 1) not in grid  # 被合并覆盖的位置不产 cell
    assert grid[(0, 2)].text == "表头C"
    # 纵向合并：vMerge 跨 2 行
    assert grid[(1, 0)].text == "竖排"
    assert grid[(1, 0)].row_span == 2
    assert grid[(1, 0)].column_span == 1
    assert (2, 0) not in grid
    # 首行 is_header 约定
    assert all(grid[(0, c)].is_header for c in (0, 2))
    assert not grid[(1, 1)].is_header
    assert asset.header_regions == ((0, 0),)


def test_paragraph_index_evidence_roundtrip(parser, normalizer, docx_bytes) -> None:
    doc = _normalize(parser, normalizer, docx_bytes)

    for el in doc.elements:
        if el.element_type != "paragraph":
            continue
        span = el.source_spans[0]
        assert span.native_ref is not None
        assert "paragraph_index" in span.native_ref
        assert isinstance(span.native_ref["paragraph_index"], int)
        assert span.raw_text == el.text
    heading_els = [e for e in doc.elements if e.element_type == "heading"]
    assert [
        e.source_spans[0].native_ref["paragraph_index"] for e in heading_els
    ] == [0, 2, 4]


def test_stable_ids_and_roundtrip_dict(parser, normalizer, docx_bytes) -> None:
    doc1 = _normalize(parser, normalizer, docx_bytes)
    doc2 = _normalize(parser, normalizer, docx_bytes)
    assert [e.element_id for e in doc1.elements] == [
        e.element_id for e in doc2.elements
    ]

    rebuilt = type(doc1).from_dict(doc1.to_dict())
    result = validate(rebuilt)
    assert result.valid
    assert [e.element_id for e in rebuilt.elements] == [
        e.element_id for e in doc1.elements
    ]


def test_rectangular_merge_row_span_exact() -> None:
    """评审 HIGH-1 回归：2列×3行矩形合并 row_span 必须=3（非 5）."""
    import io
    from docx import Document as DocxDocument
    from knowledge_mining.mining.parse_adapters.native.native_docx import (
        NativeDocxParser,
    )
    from knowledge_mining.mining.parse_adapters.normalizer import LegacyLineNormalizer  # noqa: F401

    doc = DocxDocument()
    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).merge(table.cell(2, 1))  # 矩形合并 2 列 × 3 行
    buf = io.BytesIO(); doc.save(buf)
    parser = NativeDocxParser()
    artifact = parser.parse(buf.getvalue(), mime=parser.descriptor.supported_mimes.__iter__().__next__())
    tables = [b for b in artifact.blocks if b.block_type == "table"]
    assert tables, "table block missing"
    cells = tables[0].structure["cells"]
    origins = [c for c in cells if c["row_index"] == 0 and c["column_index"] == 0]
    assert origins, "merge origin missing"
    assert origins[0]["row_span"] == 3, origins[0]
    assert origins[0]["column_span"] == 2, origins[0]
