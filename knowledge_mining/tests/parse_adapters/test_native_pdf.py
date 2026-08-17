"""NativePdfParser + PdfNormalizer 单元测试（M3, SRS §C06/§C07/§4.6-§4.7）.

fixture 策略（不新增依赖）：
- 文本/表格 PDF 用本文件内的手写最小 PDF 构造器 ``_build_pdf`` 生成
  （xref 表 + Type1 Font + content stream，经典最小模板；构造器输出的
  bytes 对给定输入是确定性的，等价于手写常量）。已用 pdfplumber 0.11.9
  验证可读。pdfplumber 安装目录不带测试 PDF 资产，无法复用。
- 加密 PDF 用 pypdf（环境内已安装，仅测试侧使用）对最小 PDF 加密。
- 合并单元格 span 推断等精细表格语义，按交付约定用"构造
  BackendParseArtifact 直喂 Normalizer"的表驱动单测覆盖；真实扫描版
  PDF 的表格验证待用户提供真实文档阶段补充。
"""
from __future__ import annotations

from io import BytesIO

import pytest

from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
    ParserAdapterError,
    UnsupportedFormat,
)
from knowledge_mining.mining.contracts.parse_ir import validate
from knowledge_mining.mining.parse_adapters.native_pdf import (
    NATIVE_PDF_FINGERPRINT,
    EncryptedDocument,
    NativePdfParser,
)
from knowledge_mining.mining.parse_adapters.pdf_normalizer import PdfNormalizer


# ---------------------------------------------------------------------------
# 手写最小 PDF 构造器（确定性 fixture）
# ---------------------------------------------------------------------------


def _build_pdf(objects: list[bytes]) -> bytes:
    """按对象体列表拼装最小合法 PDF（xref 偏移精确计算）."""
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objects) + 1
    out += f"xref\n0 {n}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {n} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _stream(content: str) -> bytes:
    return (
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"
    ).encode()


def _page(resources: str, content: str) -> list[bytes]:
    """单页 PDF 对象序列（MediaBox letter，contents 为给定流）."""
    return [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        _stream(content),
    ]


def _text_pdf() -> bytes:
    """单页：18pt 大标题一行 + 10pt 正文两行（字号启发式输入）."""
    content = (
        "BT /F1 18 Tf 72 700 Td (Big Title Here) Tj ET\n"
        "BT /F1 10 Tf 72 660 Td (Body line one.) Tj ET\n"
        "BT /F1 10 Tf 72 645 Td (Body line two.) Tj ET\n"
    )
    return _build_pdf(_page("", content))


def _two_page_pdf_with_blank() -> bytes:
    """两页：第 1 页有文本，第 2 页无文本层（空 content stream）."""
    text = "BT /F1 10 Tf 72 700 Td (Only page text.) Tj ET\n"
    return _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 6 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << >> /Contents 7 0 R >>",
        _stream(text),
        _stream(""),
    ])


def _table_pdf() -> bytes:
    """单页：线框表格（2 列 x 3 行网格 + 文本），上方一段正文."""
    content = (
        "BT /F1 10 Tf 72 700 Td (Above the table.) Tj ET\n"
        "BT /F1 10 Tf 80 665 Td (A) Tj ET\n"
        "BT /F1 10 Tf 180 665 Td (B) Tj ET\n"
        "BT /F1 10 Tf 80 635 Td (1) Tj ET\n"
        "BT /F1 10 Tf 180 635 Td (2) Tj ET\n"
        "BT /F1 10 Tf 80 605 Td (3) Tj ET\n"
        "BT /F1 10 Tf 180 605 Td (4) Tj ET\n"
        "0 w\n"
        "72 600 m 272 600 l S\n"
        "72 633 m 272 633 l S\n"
        "72 667 m 272 667 l S\n"
        "72 700 m 272 700 l S\n"
        "72 600 m 72 700 l S\n"
        "172 600 m 172 700 l S\n"
        "272 600 m 272 700 l S\n"
    )
    return _build_pdf(_page("", content))


def _encrypted_pdf() -> bytes:
    """pypdf（已安装，仅测试侧）加密的最小 PDF."""
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(BytesIO(_text_pdf()))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    writer.encrypt("secret")
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. NativePdfParser.parse
# ---------------------------------------------------------------------------


@pytest.fixture
def parser() -> NativePdfParser:
    return NativePdfParser()


def test_supports_mimes(parser: NativePdfParser) -> None:
    assert parser.supports("application/pdf")
    assert parser.supports("APPLICATION/PDF")
    assert not parser.supports("text/plain")
    assert not parser.supports("text/markdown")


def test_parse_rejects_unsupported_mime(parser: NativePdfParser) -> None:
    with pytest.raises(UnsupportedFormat):
        parser.parse(b"hello", mime="text/plain")


def test_parse_heading_and_paragraph_blocks(parser: NativePdfParser) -> None:
    """大字号行 -> heading（字号启发式），小字号行 -> paragraph."""
    artifact = parser.parse(_text_pdf(), mime="application/pdf")

    assert artifact.parser_id == "native_pdf"
    assert artifact.mime == "application/pdf"
    types = [b.block_type for b in artifact.blocks]
    assert types == ["heading", "paragraph", "paragraph"]

    heading = artifact.blocks[0]
    assert heading.text == "Big Title Here"
    assert heading.level == 1
    # 启发式标注：规则写进 structure，不冒充高置信
    assert heading.structure["heading_rule"] == "font_size_ratio"
    assert heading.structure["type_confidence"] < 0.7
    assert heading.structure["line_size"] > heading.structure["modal_size"]

    assert artifact.blocks[1].text == "Body line one."
    assert artifact.blocks[2].text == "Body line two."
    # 正文块不带 heading 启发式标注
    assert "heading_rule" not in artifact.blocks[1].structure


def test_parse_container_refs_and_bboxes(parser: NativePdfParser) -> None:
    artifact = parser.parse(_text_pdf(), mime="application/pdf")
    for block in artifact.blocks:
        assert block.container_ref == {"container_type": "page", "index": 0}
        assert block.bbox is not None
        x0, top, x1, bottom = block.bbox
        assert x0 < x1 and top < bottom


def test_parse_real_table_grid(parser: NativePdfParser) -> None:
    """线框表格 -> table 块；表格内 words 不重复出现在正文块."""
    artifact = parser.parse(_table_pdf(), mime="application/pdf")
    types = [b.block_type for b in artifact.blocks]
    assert types.count("table") == 1

    table = next(b for b in artifact.blocks if b.block_type == "table")
    assert table.container_ref == {"container_type": "page", "index": 0}
    assert table.bbox is not None
    assert table.native_ref is not None
    assert table.native_ref["table_index"] == 0
    assert table.structure["rows"] == 3
    assert table.structure["cols"] == 2
    texts = {
        (c["row_index"], c["column_index"]): c["text"]
        for c in table.structure["cells"]
    }
    assert texts[(0, 0)] == "A"
    assert texts[(0, 1)] == "B"
    assert texts[(2, 1)] == "4"

    # 表格区域内的 words 被跳过，只留表格上方的正文
    paragraphs = [b for b in artifact.blocks if b.block_type == "paragraph"]
    assert [p.text for p in paragraphs] == ["Above the table."]


def test_parse_blank_page_emits_warning_no_content(
    parser: NativePdfParser,
) -> None:
    """无文本层页 -> warning 块记录，不伪造内容."""
    artifact = parser.parse(_two_page_pdf_with_blank(), mime="application/pdf")

    page1_blocks = [
        b for b in artifact.blocks if b.container_ref
        and b.container_ref.get("index") == 1
        and b.block_type != "warning"
    ]
    assert page1_blocks == []

    warnings_on_page1 = [
        b for b in artifact.blocks if b.block_type == "warning"
        and b.container_ref
        and b.container_ref.get("index") == 1
    ]
    assert len(warnings_on_page1) == 1
    assert warnings_on_page1[0].structure["reason"] == "no_text_layer"
    assert any("no text layer" in w for w in artifact.warnings)


def test_parse_bad_bytes_raises_adapter_error(parser: NativePdfParser) -> None:
    with pytest.raises(ParserAdapterError) as excinfo:
        parser.parse(b"this is not a pdf", mime="application/pdf")
    assert not isinstance(excinfo.value, UnsupportedFormat)


def test_parse_encrypted_pdf_raises_encrypted(parser: NativePdfParser) -> None:
    with pytest.raises(EncryptedDocument):
        parser.parse(_encrypted_pdf(), mime="application/pdf")


def test_descriptor_identity(parser: NativePdfParser) -> None:
    d = parser.descriptor
    assert d.parser_id == "native_pdf"
    assert d.parser_fingerprint == "native_pdf@1.0.0#pdfplumber-0.11.9"
    assert d.supported_mimes == frozenset({"application/pdf"})
    assert {"pages", "tables", "coordinates"} <= d.capabilities


# ---------------------------------------------------------------------------
# 2. PdfNormalizer（真实 parse 产物直喂）
# ---------------------------------------------------------------------------


@pytest.fixture
def normalizer() -> PdfNormalizer:
    return PdfNormalizer()


HASH = "sha256:deadbeef"


def _normalize_text_pdf(
    parser: NativePdfParser, normalizer: PdfNormalizer
):
    artifact = parser.parse(_text_pdf(), mime="application/pdf")
    return normalizer.normalize(artifact, source_raw_hash=HASH)


def test_normalize_page_container_and_elements(
    parser: NativePdfParser, normalizer: PdfNormalizer
) -> None:
    doc = _normalize_text_pdf(parser, normalizer)

    assert len(doc.containers) == 1
    page = doc.containers[0]
    assert page.container_type == "page"
    assert page.page_number == 1  # 1 基
    assert page.order_index == 0

    assert [e.element_type for e in doc.elements] == [
        "heading", "paragraph", "paragraph",
    ]
    for element in doc.elements:
        assert element.page_span_ids == (page.container_id,)
        span = element.source_spans[0]
        assert span.page_id == page.container_id
        assert span.visual_region is not None
        assert span.visual_region["page_index"] == 0
        bbox = span.visual_region["bbox"]
        assert len(bbox) == 4 and bbox[0] < bbox[2] and bbox[1] < bbox[3]
        assert span.text_range == (0, len(element.text))
        assert span.raw_text == element.text


def test_normalize_heading_confidence_and_annotations(
    parser: NativePdfParser, normalizer: PdfNormalizer
) -> None:
    doc = _normalize_text_pdf(parser, normalizer)
    heading = doc.elements[0]
    # 字号启发式不冒充高置信
    assert heading.confidence.type is not None
    assert heading.confidence.type < 0.7
    assert heading.parser_annotations["heading_rule"] == "font_size_ratio"


def test_normalize_reading_order_and_parent_chain(
    parser: NativePdfParser, normalizer: PdfNormalizer
) -> None:
    doc = _normalize_text_pdf(parser, normalizer)
    heading, p1, p2 = doc.elements

    assert heading.parent_id is None
    assert p1.parent_id == heading.element_id
    assert p2.parent_id == heading.element_id

    next_ids = {
        r.source_element_id: r.target_element_id
        for r in doc.relations
        if r.relation_type == "next_in_reading_order"
    }
    assert next_ids == {
        heading.element_id: p1.element_id,
        p1.element_id: p2.element_id,
    }
    parent_rels = {
        r.target_element_id for r in doc.relations
        if r.relation_type == "parent_of"
    }
    assert parent_rels == {p1.element_id, p2.element_id}


def test_normalize_validates_and_roundtrips(
    parser: NativePdfParser, normalizer: PdfNormalizer
) -> None:
    doc = _normalize_text_pdf(parser, normalizer)

    result = validate(doc)
    assert result.valid, [i.message for i in result.issues]

    restored = type(doc).from_dict(doc.to_dict())
    assert validate(restored).valid
    assert restored.containers == doc.containers
    assert restored.elements == doc.elements
    assert restored.relations == doc.relations


def test_normalize_blank_page_warning_not_fabricated(
    parser: NativePdfParser, normalizer: PdfNormalizer
) -> None:
    artifact = parser.parse(
        _two_page_pdf_with_blank(), mime="application/pdf"
    )
    doc = normalizer.normalize(artifact, source_raw_hash=HASH)

    # 空白页不产生任何元素（无伪造），只留 warning
    assert len(doc.containers) == 2
    assert doc.containers[1].page_number == 2
    page1_element_ids = {
        e.element_id for e in doc.elements
        if "page-0001" in e.page_span_ids[0]
    }
    assert not page1_element_ids
    assert any("no text layer" in w for w in doc.diagnostics.warnings)
    assert validate(doc).valid


# ---------------------------------------------------------------------------
# 3. 表格映射（BackendParseArtifact 表驱动直喂 Normalizer）
# ---------------------------------------------------------------------------


def _table_artifact() -> BackendParseArtifact:
    """2x3 网格：A1 横向合并 2 列，B2 纵向合并 2 行（合并空洞为 None）.

    网格：
      [ Span2   | hole  ]
      [ x | Span2v| hole ]
      [ x | y     | z    ]
    """
    cells = [
        # (row, col, row_span, col_span, text)
        (0, 0, 1, 2, "Merged header"),
        (1, 0, 1, 1, "a"),
        (1, 1, 2, 1, "tall"),
        (2, 0, 1, 1, "b"),
        (2, 2, 1, 1, "c"),
    ]
    return BackendParseArtifact(
        parser_id="native_pdf",
        parser_version="1.0.0",
        mime="application/pdf",
        blocks=(
            BackendBlock(
                block_type="table",
                text="Merged header\ta\ttall\tb\tc",
                container_ref={"container_type": "page", "index": 0},
                bbox=(72.0, 92.0, 272.0, 192.0),
                native_ref={"page": 0, "table_index": 0},
                structure={
                    "rows": 3,
                    "cols": 3,
                    "cells": [
                        {
                            "row_index": r,
                            "column_index": c,
                            "row_span": rs,
                            "column_span": cs,
                            "text": t,
                        }
                        for r, c, rs, cs, t in cells
                    ],
                },
            ),
        ),
    )


def test_table_asset_grid_spans_and_header(
    normalizer: PdfNormalizer,
) -> None:
    doc = normalizer.normalize(_table_artifact(), source_raw_hash=HASH)

    element = doc.elements[0]
    assert element.element_type == "table"
    assert element.page_span_ids == (doc.containers[0].container_id,)
    span = element.source_spans[0]
    assert span.visual_region == {
        "bbox": [72.0, 92.0, 272.0, 192.0], "page_index": 0,
    }

    (asset,) = doc.structured_assets.values()
    assert asset.rows == 3
    assert asset.columns == 3
    assert asset.continuation_of is None  # 跨页延续留 M4 Reconciler
    by_pos = {(c.row_index, c.column_index): c for c in asset.cells}

    merged = by_pos[(0, 0)]
    assert merged.text == "Merged header"
    assert merged.column_span == 2 and merged.row_span == 1
    assert merged.is_header

    tall = by_pos[(1, 1)]
    assert tall.row_span == 2 and tall.column_span == 1
    assert not tall.is_header

    assert asset.header_regions == ((0, 0),)
    # 首行 is_header 是约定，置信度降权
    assert asset.confidence.type is not None
    assert asset.confidence.type < 0.7

    assert validate(doc).valid
