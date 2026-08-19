"""M5.2 SegmentCompiler 核心行为（RED 先行）.

业务行为验收（SRS §4.12/§5.3）：
- 标题链注入：段落切片携带祖先标题链（"章 > 节"）；
- 同标题相邻段合并（token 上限内）；
- 超上限不再合并；单体超限段落按字符二分（char_range 留痕）；
- 表格 rows 视图：整表一条 + 每行一条（行携带表头 + 表名 caption）；
- 表格 whole 视图：只整表；
- figure：caption 编译为独立切片；
- links：每条切片映射回原文元素（element_id + 证据 span）。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parse_ir.enums import (
    PARSE_IR_SCHEMA_VERSION,
)
from knowledge_mining.mining.contracts.parse_ir.types import (
    Confidence,
    Container,
    Element,
    EvidenceSpan,
    ParseIdentity,
    ParsedDocument,
    Relation,
    TableAsset,
    TableCell,
)
from knowledge_mining.mining.contracts.segment_compiler import (
    SegmentPolicy,
)


def _span(i: int) -> EvidenceSpan:
    return EvidenceSpan(span_id=f"s{i}", raw_text=f"span-{i}")


def _doc(elements, relations=(), assets=None) -> ParsedDocument:
    return ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=ParseIdentity(
            source_raw_hash="raw-1", parser_fingerprint="t@1",
            parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
        ),
        containers=(Container(container_id="c0", container_type="page", order_index=0),),
        elements=tuple(elements),
        relations=tuple(relations),
        structured_assets=assets or {},
    )


def _el(eid, etype, text, level=None, parent=None, order=None, spans=(("s",),)):
    return Element(
        element_id=eid, element_type=etype, order_index=order or 0, text=text,
        parent_id=parent,
        style={"level": level} if level else {},
        source_spans=tuple(_span(i) for i in range(1)),
    )


def _table_doc() -> ParsedDocument:
    """1 标题 + 1 表（2 行 2 列带表头）+ caption 绑定关系."""
    cells = (
        TableCell(row_index=0, column_index=0, text="告警码", is_header=True, source_span_id="s1"),
        TableCell(row_index=0, column_index=1, text="原因", is_header=True, source_span_id="s2"),
        TableCell(row_index=1, column_index=0, text="A-101", source_span_id="s3"),
        TableCell(row_index=1, column_index=1, text="风扇停转", source_span_id="s4"),
    )
    asset = TableAsset(
        table_id="t1-table", page_span_ids=("c0",), rows=2, columns=2,
        cells=cells, header_regions=((0, 0),),
        confidence=Confidence(source="t"),
    )
    elements = [
        _el("h1", "heading", "硬件告警", level=2, parent="h0", order=0),
        _el("cap1", "caption", "表 3-1 告警对照", order=1),
        _el("t1", "table", "告警码\t原因\nA-101\t风扇停转", order=2),
    ]
    relations = (
        Relation(source_element_id="cap1", target_element_id="t1",
                 relation_type="caption_of", method="reconciler"),
    )
    return _doc(elements, relations, {"t1-table": asset})


# ---------------------------------------------------------------------------
# 标题链 + 段落合并
# ---------------------------------------------------------------------------


def test_heading_chain_injected_into_paragraph_segments() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "告警处理", level=1, order=0),
        _el("h1", "heading", "硬件告警", level=2, parent="h0", order=1),
        _el("p1", "paragraph", "风扇告警需要立即处理。", parent="h1", order=2),
    ])
    segs = compile_segments(doc, SegmentPolicy())
    para = [s for s in segs if s.block_type == "paragraph"]
    assert para, segs
    assert [t for _, t in para[0].heading_chain] == ["告警处理", "硬件告警"]
    # 标题文本进入所在节的首条切片（SRS §5.3：heading + paragraphs 编译）。
    assert "硬件告警" in para[0].raw_text


def test_adjacent_paragraphs_merged_within_limit() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "章", level=1, order=0),
        _el("p1", "paragraph", "第一段内容。", parent="h0", order=1),
        _el("p2", "paragraph", "第二段内容。", parent="h0", order=2),
    ])
    segs = compile_segments(doc, SegmentPolicy(merge_adjacent_paragraphs=True))
    paras = [s for s in segs if s.block_type == "paragraph"]
    assert len(paras) == 1
    assert "第一段" in paras[0].raw_text and "第二段" in paras[0].raw_text
    assert set(paras[0].element_ids) >= {"p1", "p2"}


def test_no_merge_when_over_limit() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "章", level=1, order=0),
        _el("p1", "paragraph", "长" * 40, parent="h0", order=1),
        _el("p2", "paragraph", "短" * 20, parent="h0", order=2),
    ])
    segs = compile_segments(doc, SegmentPolicy(max_tokens=48, min_tokens=1))
    paras = [s for s in segs if s.block_type == "paragraph"]
    assert len(paras) == 2


def test_single_oversize_element_split_with_char_ranges() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    text = "字" * 300
    doc = _doc([_el("p1", "paragraph", text, order=0)])
    segs = compile_segments(doc, SegmentPolicy(max_tokens=100, min_tokens=1))
    paras = [s for s in segs if s.block_type == "paragraph"]
    assert len(paras) >= 2
    assert all(s.links[0].char_range is not None for s in paras)


# ---------------------------------------------------------------------------
# 表格类型化
# ---------------------------------------------------------------------------


def test_table_rows_view_whole_plus_rows_with_header_and_caption() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    segs = compile_segments(_table_doc(), SegmentPolicy(table_view="rows"))
    whole = [s for s in segs if s.block_type == "table"]
    rows = [s for s in segs if s.block_type == "table_row"]
    assert len(whole) == 1 and len(rows) == 1  # 1 数据行（表头行不单切）
    row = rows[0]
    assert row.metadata["table_header"] == ["告警码", "原因"]
    assert row.metadata["table_caption"] == "表 3-1 告警对照"
    assert "A-101" in row.raw_text and "风扇停转" in row.raw_text
    # 行切片映射回表格元素与该行 cell 的证据 span。
    assert row.element_ids == ("t1",)
    assert set(row.links[0].evidence_span_ids) >= {"s3", "s4"}


def test_table_whole_view_only() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    segs = compile_segments(_table_doc(), SegmentPolicy(table_view="whole"))
    assert [s.block_type for s in segs if "table" in s.block_type] == ["table"]


# ---------------------------------------------------------------------------
# figure + links + 顺序
# ---------------------------------------------------------------------------


def test_figure_caption_compiled_as_segment() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("f1", "figure", "", order=0),
        _el("cap1", "caption", "图 2 流程图", order=1),
    ], relations=(
        Relation(source_element_id="cap1", target_element_id="f1",
                 relation_type="caption_of", method="reconciler"),
    ))
    segs = compile_segments(doc, SegmentPolicy(include_figure_captions=True))
    figs = [s for s in segs if s.block_type == "figure"]
    assert len(figs) == 1
    assert "图 2 流程图" in figs[0].raw_text


def test_segments_follow_reading_order_and_carry_links() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "章", level=1, order=0),
        _el("p1", "paragraph", "甲", parent="h0", order=1),
        _el("t1", "table", "a\tb\n1\t2", order=2, spans=(("s",),)),
        _el("p2", "paragraph", "乙", order=3),
    ])
    segs = compile_segments(doc, SegmentPolicy())
    # 顺序：标题节段 → 表 → 乙（段落不会被表格吞并）。
    types = [s.block_type for s in segs]
    assert types.index("table") < types.index("paragraph", types.index("table"))
    for seg in segs:
        assert seg.links and all(l.element_id for l in seg.links)
