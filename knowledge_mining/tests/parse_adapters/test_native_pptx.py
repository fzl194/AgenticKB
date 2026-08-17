"""``NativePptxParser`` + ``PptxNormalizer`` 单元测试（M3, SRS §C06/§C07）.

fixture 用 python-pptx 在测试内生成 bytes。覆盖：slide 容器（无页码伪造）、
title placeholder -> heading level 1、文本 shape -> paragraph、
bbox 保留 EMU 坐标、表格合并、IR 断言链、错误归一。
"""
from __future__ import annotations

import io

import pytest

from knowledge_mining.mining.contracts.parse_ir import validate
from knowledge_mining.mining.contracts.parser_adapter import (
    ParserAdapterError,
    UnsupportedFormat,
)
from knowledge_mining.mining.parse_adapters.native.native_pptx import (
    NATIVE_PPTX_FINGERPRINT,
    NativePptxParser,
    PptxNormalizer,
)

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
RAW_HASH = "99" * 32

TITLE_L, TITLE_T, TITLE_W, TITLE_H = 100, 200, 300, 400  # EMU
BOX_L, BOX_T, BOX_W, BOX_H = 914400, 914400, 1828800, 457200  # EMU
BOX2_L, BOX2_T, BOX2_W, BOX2_H = 10, 20, 500, 600  # EMU


def _build_pptx_bytes() -> bytes:
    """2 页：页一 title+文本框+2x2 合并表格；页二（blank）文本框."""
    from pptx import Presentation
    from pptx.util import Emu

    prs = Presentation()
    slide1 = prs.slides.add_slide(prs.slide_layouts[1])
    slide1.shapes.title.text = "幻灯一标题"
    box = slide1.shapes.add_textbox(
        Emu(BOX_L), Emu(BOX_T), Emu(BOX_W), Emu(BOX_H)
    )
    box.text_frame.text = "第一页正文"
    table = slide1.shapes.add_table(
        2, 2, Emu(TITLE_L), Emu(TITLE_T), Emu(TITLE_W), Emu(TITLE_H)
    ).table
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(0, 0).text = "表头"
    table.cell(1, 0).text = "a"
    table.cell(1, 1).text = "b"

    slide2 = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    box2 = slide2.shapes.add_textbox(
        Emu(BOX2_L), Emu(BOX2_T), Emu(BOX2_W), Emu(BOX2_H)
    )
    box2.text_frame.text = "第二页文本"

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


@pytest.fixture
def pptx_bytes() -> bytes:
    return _build_pptx_bytes()


@pytest.fixture
def parser() -> NativePptxParser:
    return NativePptxParser()


@pytest.fixture
def normalizer() -> PptxNormalizer:
    return PptxNormalizer()


def _normalize(parser, normalizer, data: bytes):
    artifact = parser.parse(data, mime=PPTX_MIME)
    return normalizer.normalize(artifact, source_raw_hash=RAW_HASH)


# ---------------------------------------------------------------------------
# RED 1: parse 层
# ---------------------------------------------------------------------------

def test_parse_blocks_in_slide_shape_order(parser, pptx_bytes) -> None:
    artifact = parser.parse(pptx_bytes, mime=PPTX_MIME)

    assert artifact.parser_id == "native_pptx"
    assert [b.block_type for b in artifact.blocks] == [
        "heading", "paragraph", "table", "paragraph",
    ]
    assert artifact.blocks[0].container_ref == {"container_type": "slide", "index": 0}
    assert artifact.blocks[3].container_ref == {"container_type": "slide", "index": 1}
    assert artifact.blocks[0].level == 1
    # bbox 来自 shape 位置（EMU，原样保留不换算）
    assert artifact.blocks[1].bbox == (BOX_L, BOX_T, BOX_W, BOX_H)
    assert artifact.blocks[1].native_ref == {"slide_index": 0, "shape_index": 2}


def test_parse_unsupported_mime_and_corrupt(parser) -> None:
    with pytest.raises(UnsupportedFormat):
        parser.parse(b"x", mime="application/pdf")
    with pytest.raises(ParserAdapterError):
        parser.parse(b"garbage-not-ooxml", mime=PPTX_MIME)


# ---------------------------------------------------------------------------
# RED 2: IR 断言链
# ---------------------------------------------------------------------------

def test_slide_containers_no_fake_page_numbers(parser, normalizer, pptx_bytes) -> None:
    doc = _normalize(parser, normalizer, pptx_bytes)
    assert validate(doc).valid

    assert [c.container_type for c in doc.containers] == ["slide", "slide"]
    for i, container in enumerate(doc.containers):
        assert container.page_number is None  # 不伪造页码
        assert container.order_index == i
        assert container.coordinate_unit == "emu"
    # 幻灯片物理尺寸（EMU）记录在容器上
    assert doc.containers[0].width == 9144000
    assert doc.containers[0].height == 6858000


def test_element_types_heading_paragraph_table(parser, normalizer, pptx_bytes) -> None:
    doc = _normalize(parser, normalizer, pptx_bytes)

    assert [e.element_type for e in doc.elements] == [
        "heading", "paragraph", "table", "paragraph",
    ]
    heading = doc.elements[0]
    assert heading.text == "幻灯一标题"
    assert heading.style["level"] == 1
    assert heading.parent_id is None
    assert doc.elements[1].text == "第一页正文"
    assert doc.elements[3].text == "第二页文本"


def test_bbox_emu_evidence_and_slide_pages(parser, normalizer, pptx_bytes) -> None:
    doc = _normalize(parser, normalizer, pptx_bytes)

    body = doc.elements[1]
    span = body.source_spans[0]
    assert span.visual_region == {
        "bbox": [BOX_L, BOX_T, BOX_W, BOX_H],
        "unit": "emu",
    }
    # 元素挂 slide 容器：span.page_id 与 page_span_ids 都指向 slide 容器
    assert span.page_id == doc.containers[0].container_id
    assert body.page_span_ids == (doc.containers[0].container_id,)
    body2 = doc.elements[3]
    assert body2.page_span_ids == (doc.containers[1].container_id,)


def test_table_asset_with_merge(parser, normalizer, pptx_bytes) -> None:
    doc = _normalize(parser, normalizer, pptx_bytes)

    table_el = doc.elements[2]
    asset = doc.structured_assets[f"{table_el.element_id}-table"]
    assert asset.rows == 2
    assert asset.columns == 2
    grid = {(c.row_index, c.column_index): c for c in asset.cells}
    assert grid[(0, 0)].text == "表头"
    assert grid[(0, 0)].column_span == 2
    assert (0, 1) not in grid
    assert grid[(1, 0)].text == "a"
    assert grid[(1, 1)].text == "b"
    assert grid[(0, 0)].is_header


def test_ir_chain_relations_and_fingerprint(parser, normalizer, pptx_bytes) -> None:
    doc = _normalize(parser, normalizer, pptx_bytes)
    assert validate(doc).valid

    reading = [
        r for r in doc.relations if r.relation_type == "next_in_reading_order"
    ]
    assert len(reading) == 3
    ids = [e.element_id for e in doc.elements]
    assert [(r.source_element_id, r.target_element_id) for r in reading] == list(
        zip(ids, ids[1:])
    )
    assert doc.source_identity.parser_fingerprint == NATIVE_PPTX_FINGERPRINT
    assert doc.source_identity.parser_fingerprint == (
        "native_pptx@1.0.0#python-pptx-1.0.2"
    )
