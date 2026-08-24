"""segment-compiler@2 新行为验收（2026-08 切片整改）.

业务规格（用户拍板档位 max=2048 / min=512 / 表格默认整表）：
- 小片治理：同章节孤立小片（引导句/标题/尾片）并入相邻切片；紧跟表格
  的引导句并入表格片作前缀（表格身份不变）；
- 表格原子性：整表永不与正文合并、永不因过小被吞；超限整表按完整
  数据行分组降级（每组重复表头），不字符硬切；
- 代码/长文本：行边界切分，无换行的超长文本字符兜底，尾片并入前片；
- 语义标注：semantic_role（章节标题模式）+ table_kind（表头判别）。
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
from knowledge_mining.mining.contracts.segment_compiler import SegmentPolicy


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


def _el(eid, etype, text, level=None, parent=None, order=None):
    return Element(
        element_id=eid, element_type=etype, order_index=order or 0, text=text,
        parent_id=parent,
        style={"level": level} if level else {},
        source_spans=(_span(1),),
    )


def _asset(table_id, rows, columns, header_row=True) -> TableAsset:
    cells = []
    for r in range(rows):
        for c in range(columns):
            cells.append(TableCell(
                row_index=r, column_index=c,
                text=f"h{c}" if (header_row and r == 0) else f"r{r}c{c}",
                is_header=header_row and r == 0,
                source_span_id=f"sp{r}{c}",
            ))
    return TableAsset(
        table_id=table_id, page_span_ids=("c0",), rows=rows, columns=columns,
        cells=tuple(cells), header_regions=((0, 0),) if header_row else (),
        confidence=Confidence(source="t"),
    )


def _table_text(rows, columns) -> str:
    return "\n".join(
        "\t".join(f"h{c}" if r == 0 else f"r{r}c{c}" for c in range(columns))
        for r in range(rows)
    )


# ---------------------------------------------------------------------------
# 小片治理（min_tokens 生效）
# ---------------------------------------------------------------------------


def test_lead_in_sentence_merges_into_following_table_as_prefix() -> None:
    """表格引导句并入表格片前缀：一片读完整，表格身份/元数据不变."""
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "字段定义", level=1, order=0),
        _el("p1", "paragraph", "保留的稳定关系：", parent="h0", order=1),
        _el("t1", "table", _table_text(3, 3), parent="h0", order=2),
    ], assets={"t1-table": _asset("t1-table", 3, 3)})
    segs = compile_segments(doc, SegmentPolicy())
    tables = [s for s in segs if s.block_type == "table"]
    assert len(tables) == 1
    table = tables[0]
    # 标题 + 引导句都并入表格前缀（同章节小片治理），表格身份保留。
    assert table.raw_text.startswith("字段定义\n保留的稳定关系：")
    assert table.metadata["view"] == "whole"
    assert set(table.element_ids) >= {"t1", "p1"}  # 溯源合并
    # 引导句不再独立成片。
    paras = [s for s in segs if s.block_type == "paragraph" and s.raw_text == "保留的稳定关系："]
    assert not paras


def test_small_heading_absorbs_paragraph_and_upgrades_identity() -> None:
    """纯标题段吸收小正文后身份升级为正文类型（标题转正文前缀）."""
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "章", level=1, order=0),
        _el("h1", "heading", "节", level=2, parent="h0", order=1),
        _el("p1", "paragraph", "正文内容。", parent="h1", order=2),
    ])
    segs = compile_segments(doc, SegmentPolicy())
    assert len(segs) == 1
    seg = segs[0]
    assert seg.block_type == "paragraph"  # 不再是 heading 导航段
    assert seg.raw_text.startswith("章") and "正文内容。" in seg.raw_text
    assert [t for _, t in seg.heading_chain] == ["章", "节"]  # 取更深链


def test_cross_section_small_fragment_not_merged() -> None:
    """跨章节（兄弟节）不合并：结构边界优先于小片治理."""
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "3 静态", level=1, order=0),
        _el("h1", "heading", "3.1 节", level=2, parent="h0", order=1),
        _el("p1", "paragraph", "上一节的收尾段。" * 20, parent="h1", order=2),
        _el("h2", "heading", "3.2 节", level=2, parent="h0", order=3),
        _el("p2", "paragraph", "下一节开头。", parent="h2", order=4),
        _el("t1", "table", _table_text(3, 3), parent="h2", order=5),
    ], assets={"t1-table": _asset("t1-table", 3, 3)})
    segs = compile_segments(doc, SegmentPolicy())
    # 3.1 收尾段不得并入 3.2 的表格（异章节）。
    table = next(s for s in segs if s.block_type == "table")
    assert "上一节的收尾段" not in table.raw_text
    # 3.2 的引导句仍并入 3.2 的表格。
    assert "下一节开头。" in table.raw_text


def test_min_merge_disabled_when_merge_adjacent_off() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "章", level=1, order=0),
        _el("p1", "paragraph", "短句。", parent="h0", order=1),
        _el("t1", "table", _table_text(2, 2), parent="h0", order=2),
    ], assets={"t1-table": _asset("t1-table", 2, 2)})
    segs = compile_segments(doc, SegmentPolicy(merge_adjacent_paragraphs=False))
    table = next(s for s in segs if s.block_type == "table")
    assert "短句。" not in table.raw_text  # 关闭合并后引导句保持独立


# ---------------------------------------------------------------------------
# 超限整表降级（行分组 + 表头前缀）
# ---------------------------------------------------------------------------


def test_oversize_whole_table_degrades_to_row_groups_with_header() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    rows, columns = 8, 4
    doc = _doc([
        _el("t1", "table", _table_text(rows, columns), order=0),
    ], assets={"t1-table": _asset("t1-table", rows, columns)})
    # 上限压到两组行：每组必须带表头前缀，行不跨组拆断。
    segs = compile_segments(
        doc, SegmentPolicy(max_tokens=120, min_tokens=8, table_view="whole"),
    )
    tables = [s for s in segs if s.block_type == "table"]
    assert len(tables) >= 2
    for seg in tables:
        assert seg.raw_text.startswith("h0\th1\th2\th3")  # 每组重复表头
        assert seg.metadata["view"] == "whole"
        assert seg.metadata["split"] == "row_group"
    # 无组内断行：每行文本（h*/r*c*）在某一组内完整出现。
    joined = "\n".join(s.raw_text for s in tables)
    for r in range(1, rows):
        line = "\t".join(f"r{r}c{c}" for c in range(columns))
        assert line in joined


def test_small_table_never_merged_and_never_absorbed() -> None:
    """小表原子：不吸收邻片，也不被邻片吞并（工业界惯例）."""
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "章", level=1, order=0),
        _el("t1", "table", _table_text(2, 2), parent="h0", order=1),
        _el("p1", "paragraph", "表格后面的短段。", parent="h0", order=2),
    ], assets={"t1-table": _asset("t1-table", 2, 2)})
    segs = compile_segments(doc, SegmentPolicy())
    table = next(s for s in segs if s.block_type == "table")
    assert "表格后面的短段" not in table.raw_text  # 不后吸收
    # 标题"章"并入表格前缀（正确行为）；表体完整且不吞后续段落。
    assert table.raw_text.endswith(_table_text(2, 2))
    assert len([s for s in segs if s.block_type == "table"]) == 1


# ---------------------------------------------------------------------------
# 长文本切分（行边界 + 字符兜底 + 尾片保护）
# ---------------------------------------------------------------------------


def test_oversize_code_splits_at_line_boundaries_with_tail_protection() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    lines = [f"line-{i:03d} " + "x" * 40 for i in range(20)]
    doc = _doc([_el("c1", "code", "\n".join(lines), order=0)])
    segs = compile_segments(
        doc, SegmentPolicy(max_tokens=200, min_tokens=100),
    )
    codes = [s for s in segs if s.block_type == "code"]
    assert len(codes) >= 2
    for seg in codes:
        for line in seg.raw_text.split("\n"):
            # 行内不被截断（完整行来自原文某一行）。
            assert line in lines or not line
    # 尾片保护：最后一片不小于 min（除非文档本身行数不足）。
    assert len(codes[-1].raw_text) >= 100 or len(codes) == 1


def test_no_newline_long_text_still_split_by_chars() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([_el("p1", "paragraph", "字" * 300, order=0)])
    segs = compile_segments(
        doc, SegmentPolicy(max_tokens=100, min_tokens=1),
    )
    paras = [s for s in segs if s.block_type == "paragraph"]
    assert len(paras) == 3  # 300 / 100
    # char_range 连续覆盖全文。
    ranges = [p.links[0].char_range for p in paras]
    assert ranges[0][0] == 0 and ranges[-1][1] == 300
    for (_, end), (start, _) in zip(ranges, ranges[1:]):
        assert end == start


# ---------------------------------------------------------------------------
# 语义标注（semantic_role / table_kind）
# ---------------------------------------------------------------------------


def test_semantic_role_derived_from_section_titles() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "4.1.2 task_layer 枚举", level=2, order=0),
        _el("p1", "paragraph", "atom / compound / feature。", parent="h0", order=1),
    ])
    segs = compile_segments(doc, SegmentPolicy())
    assert segs and segs[0].semantic_role == "enumeration"

    doc2 = _doc([
        _el("h0", "heading", "9.1 禁止关系", level=2, order=0),
        _el("p1", "paragraph", "不得出现的边。", parent="h0", order=1),
    ])
    segs2 = compile_segments(doc2, SegmentPolicy())
    assert segs2 and segs2[0].semantic_role == "constraint"

    doc3 = _doc([
        _el("h0", "heading", "11. 当前结论", level=2, order=0),
        _el("p1", "paragraph", "综上。", parent="h0", order=1),
    ])
    segs3 = compile_segments(doc3, SegmentPolicy())
    assert segs3 and segs3[0].semantic_role == "conclusion"


def test_table_kind_detected_from_headers() -> None:
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    def _kind(header_cells):
        cells = tuple(
            TableCell(row_index=0, column_index=i, text=t, is_header=True,
                      source_span_id=f"h{i}")
            for i, t in enumerate(header_cells)
        ) + (
            TableCell(row_index=1, column_index=0, text="v", is_header=False,
                      source_span_id="d0"),
        )
        asset = TableAsset(
            table_id="t1-table", page_span_ids=("c0",), rows=2,
            columns=len(header_cells), cells=cells, header_regions=((0, 0),),
            confidence=Confidence(source="t"),
        )
        doc = _doc(
            [_el("t1", "table", "\t".join(header_cells) + "\nv", order=0)],
            assets={"t1-table": asset},
        )
        segs = compile_segments(doc, SegmentPolicy())
        return segs[0].metadata["table_kind"]

    assert _kind(("起点", "关系", "终点")) == "relation_table"
    assert _kind(("对象", "中文名", "定位")) == "definition_table"
    assert _kind(("a", "b")) == "generic_table"


def test_default_policy_is_large_window_whole_table() -> None:
    """用户拍板的默认档位：max=2048 / min=512 / 表格整表."""
    policy = SegmentPolicy()
    assert policy.max_tokens == 2048
    assert policy.min_tokens == 512
    assert policy.table_view == "whole"


# ---------------------------------------------------------------------------
# 单行章节兜底吸收（v2.1，跨章节合并的唯一例外）
# 实盘来源：2026-08-24 GWFD 特性文档（概述.md 等）的单行样板节成串残留
# ---------------------------------------------------------------------------


def test_one_liner_boilerplate_sections_absorbed_into_adjacent_text() -> None:
    """成串单行样板节（对系统的影响/应用限制）并入前邻正文片，不再孤立."""
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "原理概述", level=1, order=0),
        _el("p1", "paragraph", "正文内容。" * 30, parent="h0", order=1),
        _el("h1", "heading", "对系统的影响", level=1, order=2),
        _el("p2", "paragraph", "本特性对系统无影响。", parent="h1", order=3),
        _el("h2", "heading", "应用限制", level=1, order=4),
        _el("p3", "paragraph", "本特性无应用限制。", parent="h2", order=5),
    ])
    segs = compile_segments(doc, SegmentPolicy())
    assert len(segs) == 1  # 两个微型节均并入正文宿主
    text = segs[0].raw_text
    assert "本特性对系统无影响。" in text and "本特性无应用限制。" in text
    # 文档顺序保持：正文 → 影响 → 限制。
    assert (
        text.index("正文内容。") < text.index("本特性对系统无影响。")
        < text.index("本特性无应用限制。")
    )


def test_micro_section_between_table_atoms_joins_next_table_prefix() -> None:
    """夹在两表之间的微型节（License支持）并入后表前缀，表格身份不变."""
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "与其他特性的交互", level=1, order=0),
        _el("t1", "table", _table_text(4, 3), parent="h0", order=1),
        _el("h1", "heading", "License支持", level=1, order=2),
        _el("p1", "paragraph", "本特性无需获得License许可。", parent="h1", order=3),
        _el("h2", "heading", "遵循标准", level=1, order=4),
        _el("t2", "table", _table_text(4, 3), parent="h2", order=5),
    ], assets={
        "t1-table": _asset("t1-table", 4, 3),
        "t2-table": _asset("t2-table", 4, 3),
    })
    segs = compile_segments(doc, SegmentPolicy())
    tables = [s for s in segs if s.block_type == "table"]
    assert len(tables) == 2
    assert "License" not in tables[0].raw_text  # 前表不后吸收异章节微型节
    # 后表以微型节文本为前缀（顺序保持），表格身份/元数据不变。
    assert tables[1].raw_text.startswith("License支持")
    assert tables[1].raw_text.endswith(_table_text(4, 3))
    assert tables[1].metadata["view"] == "whole"
    # 全文不再有孤立微型文本片（原子片除外）。
    assert all(
        len(s.raw_text) >= 48 or s.block_type in ("table", "table_row", "figure")
        for s in segs
    )


def test_micro_section_at_doc_head_joins_next_section() -> None:
    """文档开头的微型节（适用NF）并入后邻章节片作前缀."""
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "适用NF", level=1, order=0),
        _el("p1", "paragraph", "PGW-U、UPF。", parent="h0", order=1),
        _el("h1", "heading", "定义", level=1, order=2),
        _el("p2", "paragraph", "两种地址分配方式。" * 20, parent="h1", order=3),
    ])
    segs = compile_segments(doc, SegmentPolicy())
    assert len(segs) == 1
    assert segs[0].raw_text.startswith("适用NF")  # 顺序保持：微型节在前
    assert segs[0].block_type == "paragraph"


def test_section_above_micro_floor_stays_standalone() -> None:
    """高于吸收线（48 token）的短节保持独立——仍有独立检索价值."""
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    doc = _doc([
        _el("h0", "heading", "命令", level=1, order=0),
        _el("p1", "paragraph", "本特性相关的MML命令说明。" * 14, parent="h0", order=1),
        _el("h1", "heading", "告警", level=1, order=2),
        _el("p2", "paragraph", "本特性无相关告警。", parent="h1", order=3),
        _el("h2", "heading", "测量指标", level=1, order=4),
        _el("p3", "paragraph",
            "1914307807 用户平面使用外部IPv4地址当前的会话数目。\n"
            "1914308021 用户平面指定APN/DNN当前使用外部IPv4地址的会话数目。",
            parent="h2", order=5),
    ])
    segs = compile_segments(doc, SegmentPolicy())
    # 告警（微型）并入命令片；测量指标（两行指标 > 48）保持独立。
    assert len(segs) == 2
    assert "本特性无相关告警。" in segs[0].raw_text
    assert segs[1].raw_text.startswith("测量指标")


def test_micro_absorption_respects_max_tokens() -> None:
    """前后邻均接近 max_tokens 时微型片保序独立，不越限合并."""
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    body = "长" * 2040
    doc = _doc([
        _el("h0", "heading", "前节", level=1, order=0),
        _el("p1", "paragraph", body, parent="h0", order=1),
        _el("h1", "heading", "微型节", level=1, order=2),
        _el("p2", "paragraph", "微型内容。", parent="h1", order=3),
        _el("h2", "heading", "后节", level=1, order=4),
        _el("p3", "paragraph", body, parent="h2", order=5),
    ])
    segs = compile_segments(doc, SegmentPolicy())
    assert len(segs) == 3  # 前节 / 微型节（保序独立）/ 后节
    assert segs[1].raw_text == "微型节\n微型内容。"
    assert segs[0].raw_text.endswith(body) and segs[2].raw_text.endswith(body)
