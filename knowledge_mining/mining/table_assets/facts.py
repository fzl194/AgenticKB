"""表格 cell 类型化事实抽取（A3，39 号 §3.1）——纯函数，只依赖 Parse IR 契约.

IR ``TableCell`` 一直保存 value_type/normalized_value/formula/row_span/
column_span/source_span_id（37 号 §1.1 盘点：投影层丢弃）——本模块把它们
整理为投影/回填两侧共用的查询结构。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument


@dataclass(frozen=True)
class CellFact:
    """单个 cell 的类型化事实（列名/原始值在 cells 行已有，不重复）。"""

    value_type: str | None
    normalized_value: str | None
    formula: str | None
    row_span: int | None
    column_span: int | None
    source_span_id: str | None


@dataclass(frozen=True)
class TableFacts:
    """快照内全部表格事实：cells 键 = (table_ref, row, column_index)。"""

    cells: dict[tuple[str, int, int], CellFact]
    sheets: dict[str, str]

    def cell(self, table_ref: str, row: int, column_index: int) -> CellFact | None:
        return self.cells.get((table_ref, row, column_index))

    def sheet_of(self, table_ref: str) -> str | None:
        return self.sheets.get(table_ref)


def _sheet_name_of(doc: ParsedDocument, page_span_ids: Sequence[str]) -> str | None:
    for container_id in page_span_ids:
        container = next(
            (c for c in doc.containers if c.container_id == container_id), None
        )
        if container is not None and container.container_type == "sheet":
            return container.name
    return None


def extract_table_facts(doc: ParsedDocument) -> TableFacts:
    """IR → 表格事实索引（确定性纯函数）."""
    cells: dict[tuple[str, int, int], CellFact] = {}
    sheets: dict[str, str] = {}
    for asset in doc.structured_assets.values():
        table_ref = asset.table_id
        for cell in asset.cells:
            cells[(table_ref, cell.row_index, cell.column_index)] = CellFact(
                value_type=cell.value_type,
                normalized_value=cell.normalized_value,
                formula=cell.formula,
                row_span=cell.row_span if cell.row_span != 1 else None,
                column_span=cell.column_span if cell.column_span != 1 else None,
                source_span_id=cell.source_span_id,
            )
        sheet = _sheet_name_of(doc, asset.page_span_ids)
        if sheet:
            sheets[table_ref] = sheet
    return TableFacts(cells=cells, sheets=sheets)


def plan_cell_updates(
    rows: Sequence[Mapping[str, Any]],
    facts: TableFacts,
) -> list[dict[str, Any]]:
    """回填 UPDATE 计划（幂等）：只产出 ``value_type IS NULL`` 的行的补齐参数.

    已回填行（value_type 非空）与事实缺失 cell（如表头行）保持不动——
    重放二跑零变更。
    """
    updates: list[dict[str, Any]] = []
    for row in rows:
        if row.get("value_type"):
            continue
        fact = facts.cell(
            str(row["table_ref"]), int(row["row"]), int(row["column_index"]),
        )
        if fact is None:
            continue
        updates.append({
            "table_ref": row["table_ref"],
            "row": row["row"],
            "column_index": row["column_index"],
            "value_type": fact.value_type,
            "normalized_value": fact.normalized_value,
            "formula": fact.formula,
            "row_span": fact.row_span,
            "column_span": fact.column_span,
            "source_span_id": fact.source_span_id,
        })
    return updates


__all__ = ["CellFact", "TableFacts", "extract_table_facts", "plan_cell_updates"]
