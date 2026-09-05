"""29号复审 R02（Wave 1）：表格 identity 与 cell 事实通道的跨层契约.

从**真实 TableAsset**（经 compile_segments，不手工构造 CompiledSegment）
验证：
- whole/rows 共享同一 table_ref（ParseIR table_id）；
- 重复表头确定性消歧（参数、参数 → 参数、参数#2）；
- caption 前缀不再吞掉首列（row_cells 精确事实，非文本反解析）；
- 值含 ；/= 不丢 cell；
- 结构面 N 行 × M 列 cells、单一 table asset、真实 row_count；
- projector row target 格式 {doc}#table_row:{table_ref}:{row_index} 与
  检索侧 TargetRefFormat 一致；container_ref 指回整表。
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


def _el(eid, etype, text, level=None, parent=None, order=None):
    return Element(
        element_id=eid, element_type=etype, order_index=order or 0, text=text,
        parent_id=parent,
        style={"level": level} if level else {},
        source_spans=tuple(_span(i) for i in range(1)),
    )


def _tricky_table_doc() -> ParsedDocument:
    """2 数据行 × 3 列；重复表头；caption；值含 ；与 =。"""
    cells = (
        TableCell(row_index=0, column_index=0, text="参数", is_header=True),
        TableCell(row_index=0, column_index=1, text="参数", is_header=True),
        TableCell(row_index=0, column_index=2, text="功耗W", is_header=True),
        TableCell(row_index=1, column_index=0, text="A-101"),
        TableCell(row_index=1, column_index=1, text="温度；上限=85"),
        TableCell(row_index=1, column_index=2, text="30"),
        TableCell(row_index=2, column_index=0, text="A-102"),
        TableCell(row_index=2, column_index=1, text="正常"),
        TableCell(row_index=2, column_index=2, text="45"),
    )
    asset = TableAsset(
        table_id="t9-table", page_span_ids=("c0",), rows=3, columns=3,
        cells=cells, header_regions=((0, 0),),
        confidence=Confidence(source="t"),
    )
    elements = [
        _el("h0", "heading", "规格表", level=2, order=0),
        _el("cap9", "caption", "表 3-1 告警对照", order=1),
        _el(
            "t9", "table",
            "参数\t参数\t功耗W\nA-101\t温度；上限=85\t30\nA-102\t正常\t45",
            order=2,
        ),
    ]
    relations = (
        Relation(source_element_id="cap9", target_element_id="t9",
                 relation_type="caption_of", method="reconciler"),
    )
    return ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=ParseIdentity(
            source_raw_hash="raw-9", parser_fingerprint="t@1",
            parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
        ),
        containers=(Container(container_id="c0", container_type="page",
                              order_index=0),),
        elements=tuple(elements),
        relations=relations,
        structured_assets={"t9-table": asset},
    )


def _compiled():
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    return compile_segments(
        _tricky_table_doc(), SegmentPolicy(table_view="both"),
    )


def test_compiler_propagates_shared_table_identity():
    segs = _compiled()
    whole = [s for s in segs if s.block_type == "table"]
    rows = [s for s in segs if s.block_type == "table_row"]
    assert whole and len(rows) == 2
    refs = {s.metadata["table_ref"] for s in whole + rows}
    assert refs == {"t9-table"}, refs


def test_compiler_dedups_duplicate_headers_deterministically():
    rows = [s for s in _compiled() if s.block_type == "table_row"]
    header = rows[0].metadata["table_header"]
    assert header == ["参数", "参数#2", "功耗W"], header


def test_row_cells_are_exact_facts_not_reparsed_text():
    rows = sorted(
        (s for s in _compiled() if s.block_type == "table_row"),
        key=lambda s: s.metadata["row_index"],
    )
    # caption 前缀 + 值含 ；/= 均不影响精确事实
    # （36号根因 6：三元组携带真实列号）
    assert rows[0].metadata["row_cells"] == [
        ["参数", "A-101", 0], ["参数#2", "温度；上限=85", 1], ["功耗W", "30", 2],
    ]
    assert rows[1].metadata["row_cells"] == [
        ["参数", "A-102", 0], ["参数#2", "正常", 1], ["功耗W", "45", 2],
    ]
    # 展示文本保留 caption 前缀（自描述，供 FTS）
    assert rows[0].raw_text.startswith("[表 3-1 告警对照] ")


def test_structure_projection_single_asset_with_full_cells():
    from knowledge_mining.mining.retrieval_projection.structure_projection import (
        project_structure,
    )

    structure = project_structure(_compiled(), document_ref="spec.md")
    assets = [a for a in structure.table_assets if a["table_ref"] == "t9-table"]
    assert len(assets) == 1
    assert assets[0]["row_count"] == 2  # 真实数据行数（非 max+1）
    assert assets[0]["columns"] == ["参数", "参数#2", "功耗W"]
    cells = [c for c in structure.table_cells if c["table_ref"] == "t9-table"]
    assert len(cells) == 6  # 2 行 × 3 列，无一丢失
    row1 = {c["column"]: c["value"] for c in cells if c["row"] == 1}
    assert row1 == {"参数": "A-101", "参数#2": "温度；上限=85", "功耗W": "30"}
    # caption 不再吞首列：参数列在
    assert "参数" in row1


def test_projector_row_target_matches_serving_format():
    from knowledge_mining.mining.retrieval_projection.projector import (
        project_representations,
    )

    reps = project_representations(
        _compiled(), document_ref="spec.md", snapshot_ref="snap_1",
    )
    table = next(r for r in reps if r.representation_type == "table")
    row = next(r for r in reps if r.representation_type == "table_row")
    assert table.target_ref == "spec.md#table:t9-table"
    # 检索侧 TargetRefFormat：{document_ref}#table_row:{table_ref}:{row_index}
    assert row.target_ref == "spec.md#table_row:t9-table:1"
    assert row.container_ref == "t9-table"
    # caption 进入结构上下文（表格语境）
    assert "表 3-1" in row.structural_context


def test_raw_projection_roundtrip_preserves_table_facts():
    from knowledge_mining.mining.segment_compiler.projection import (
        to_raw_segment_data,
    )

    rows = [s for s in _compiled() if s.block_type == "table_row"]
    rsd = to_raw_segment_data(rows[0], document_key="spec.md")
    payload = rsd.structure_json
    assert payload["source_block_type"] == "table_row"
    assert payload["table_ref"] == "t9-table"
    assert payload["row_cells"] == [
        ["参数", "A-101", 0], ["参数#2", "温度；上限=85", 1], ["功耗W", "30", 2],
    ]


def test_parse_layer_dedups_duplicate_columns_without_losing_values():
    """29号 R02（E2E 追溯发现的 parse 层根因）：markdown/html 表行以
    dict（列名→值）流转，重名列静默覆盖丢值（首列值被后列顶掉）——
    表头捕获时确定性消歧。"""
    import dataclasses

    from knowledge_mining.mining.parse_adapters.legacy_markdown import (
        LegacyMarkdownParser,
    )

    md = (
        "# 规格\n\n"
        "| 参数 | 参数 | 门限W |\n"
        "|---|---|---|\n"
        "| A-301 | 温度；上限=85 | 30 |\n"
        "| A-302 | 正常 | 45 |\n"
    ).encode("utf-8")
    art = LegacyMarkdownParser().parse(md, mime="text/markdown")
    table = next(
        dataclasses.asdict(b) if dataclasses.is_dataclass(b) else vars(b)
        for b in art.blocks
        if getattr(b, "block_type", None) == "table"
        or (getattr(b, "block_type", None) is None
            and vars(b).get("block_type") == "table")
    )
    structure = table["structure"]
    assert structure["columns"] == ["参数", "参数#2", "门限W"]
    # 首列值不再被同名列覆盖
    assert structure["rows"][0] == {
        "参数": "A-301", "参数#2": "温度；上限=85", "门限W": "30",
    }
    assert "A-301" in table["text"]
