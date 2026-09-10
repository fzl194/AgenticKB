"""A3（34 号 P0-5；39 号 §3.1）：表格 cell 类型化事实投影.

契约：
- IR TableCell 的 value_type/normalized_value/formula/row_span/column_span/
  source_span_id 完整进入投影（此前在编译/投影两跳被丢弃）；
- xlsx sheet 名从容器树落到 asset_structured_assets.sheet_name；
- structure_projection 消费 facts：cell 行增列、表资产行增 sheet_name；
- 无 facts（IR 缺失/旧链路）行为与旧版一致（列全 NULL）；
- 回填 UPDATE 计划幂等（只更新缺失列）。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parse_ir.enums import (
    PARSE_IR_SCHEMA_VERSION,
)
from knowledge_mining.mining.contracts.parse_ir.types import (
    Container,
    Element,
    EvidenceSpan,
    ParseIdentity,
    ParsedDocument,
    TableAsset,
    TableCell,
)
from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment
from knowledge_mining.mining.retrieval_projection.structure_projection import (
    project_structure,
)
from knowledge_mining.mining.table_assets.facts import (
    extract_table_facts,
    plan_cell_updates,
)


def _doc() -> ParsedDocument:
    return ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=ParseIdentity(
            source_raw_hash="raw-1", parser_fingerprint="native-xlsx@1",
            parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
        ),
        containers=(
            Container(
                container_id="sheet-1", container_type="sheet", order_index=0,
                name="告警表",
            ),
        ),
        elements=(
            Element(
                element_id="t-el", element_type="table", order_index=0,
                text="告警表", style={},
                source_spans=(EvidenceSpan(span_id="s0", raw_text="t"),),
            ),
        ),
        structured_assets={
            "t-el-table": TableAsset(
                table_id="tbl:alarm",
                rows=2,
                columns=2,
                page_span_ids=("sheet-1",),
                cells=(
                    TableCell(
                        row_index=0, column_index=0, text="A1-101",
                        value_type="text",
                    ),
                    TableCell(
                        row_index=0, column_index=1, text="1,234.5",
                        normalized_value="1234.5", value_type="number",
                        source_span_id="span-a",
                    ),
                    TableCell(
                        row_index=1, column_index=0, text="2026-09-07",
                        normalized_value="2026-09-07", value_type="date",
                    ),
                    TableCell(
                        row_index=1, column_index=1, text="=SUM(B1)",
                        normalized_value="10", value_type="number",
                        formula="=SUM(B1)", row_span=2,
                    ),
                ),
            ),
        },
    )


def _row_segment() -> CompiledSegment:
    return CompiledSegment(
        segment_index=0,
        block_type="table_row",
        raw_text="告警码=A1-101",
        element_ids=("t-el",),
        metadata={
            "table_ref": "tbl:alarm", "table_header": ["告警码", "功耗"],
            "row_index": 0,
            "row_cells": [["告警码", "A1-101", 0], ["功耗", "1,234.5", 1]],
        },
    )


# ---------------------------------------------------------------------------
# extract：IR → facts
# ---------------------------------------------------------------------------


def test_extract_carries_all_cell_facts_and_sheet_name():
    facts = extract_table_facts(_doc())
    number_cell = facts.cells[("tbl:alarm", 0, 1)]
    assert number_cell.normalized_value == "1234.5"
    assert number_cell.value_type == "number"
    assert number_cell.source_span_id == "span-a"
    formula_cell = facts.cells[("tbl:alarm", 1, 1)]
    assert formula_cell.formula == "=SUM(B1)"
    assert formula_cell.row_span == 2
    assert facts.cells[("tbl:alarm", 1, 0)].value_type == "date"
    assert facts.sheets["tbl:alarm"] == "告警表"


# ---------------------------------------------------------------------------
# structure_projection 消费 facts
# ---------------------------------------------------------------------------


def test_projection_enriches_cells_and_sheet():
    facts = extract_table_facts(_doc())
    structure = project_structure(
        (_row_segment(),), document_ref="alarm.xlsx", table_facts=facts,
    )
    cells = {(c["column_index"]): c for c in structure.table_cells}
    assert cells[0]["value_type"] == "text"
    assert cells[1]["normalized_value"] == "1234.5"
    assert cells[1]["value_type"] == "number"
    assert cells[1]["source_span_id"] == "span-a"
    asset = structure.table_assets[0]
    assert asset["sheet_name"] == "告警表"


def test_projection_without_facts_keeps_legacy_shape():
    structure = project_structure(
        (_row_segment(),), document_ref="alarm.xlsx",
    )
    cell = structure.table_cells[0]
    assert cell.get("value_type") is None
    assert cell.get("normalized_value") is None
    assert structure.table_assets[0].get("sheet_name") is None


# ---------------------------------------------------------------------------
# 回填 UPDATE 计划（幂等：只补缺失列）
# ---------------------------------------------------------------------------


def test_plan_cell_updates_only_missing_columns():
    facts = extract_table_facts(_doc())
    existing = [
        # 已回填过的行（value_type 非空）→ 跳过
        {"table_ref": "tbl:alarm", "row": 0, "column_index": 0,
         "value_type": "text", "normalized_value": None,
         "formula": None, "row_span": None, "column_span": None,
         "source_span_id": None},
        # 缺失行 → 全列补齐
        {"table_ref": "tbl:alarm", "row": 0, "column_index": 1,
         "value_type": None, "normalized_value": None,
         "formula": None, "row_span": None, "column_span": None,
         "source_span_id": None},
        # 事实里没有的 cell（如表头行）→ 保持不动
        {"table_ref": "tbl:alarm", "row": 9, "column_index": 9,
         "value_type": None, "normalized_value": None,
         "formula": None, "row_span": None, "column_span": None,
         "source_span_id": None},
    ]
    updates = plan_cell_updates(existing, facts)
    assert len(updates) == 1
    assert updates[0]["row"] == 0 and updates[0]["column_index"] == 1
    assert updates[0]["normalized_value"] == "1234.5"
    assert updates[0]["source_span_id"] == "span-a"
