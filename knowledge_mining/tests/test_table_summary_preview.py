"""_table_summary 契约（2026-09-01 用户反馈修复）：

preview 不得含表头行（前端列 label 已渲染表头，数据区再出现即重复）；
rows = 数据行数（与切片行片数/结构化查询一致，不含表头）；preview 有界。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parse_ir.types import (
    TableAsset,
    TableCell,
)
from knowledge_mining.mining.snapshot_store.read_service import (
    _PREVIEW_ROW_LIMIT,
    _table_summary,
)


def _asset(rows: int, cols: int = 3, header: bool = True) -> TableAsset:
    total = rows + (1 if header else 0)
    cells = tuple(
        TableCell(
            row_index=r,
            column_index=c,
            text=f"r{r}c{c}",
            is_header=header and r == 0,
        )
        for r in range(total)
        for c in range(cols)
    )
    return TableAsset(
        table_id="t1",
        page_span_ids=(),
        rows=total,
        columns=cols,
        cells=cells,
        header_regions=((0, 0),) if header else (),
    )


def test_preview_excludes_header_row_and_rows_counts_data_only() -> None:
    summary = _table_summary(_asset(rows=23))
    assert summary["rows"] == 23
    assert summary["header"] == ["r0c0", "r0c1", "r0c2"]
    assert len(summary["preview"]) == 23
    # 数据行从表头之后开始——首行不得再是表头文本
    assert summary["preview"][0] == ["r1c0", "r1c1", "r1c2"]
    assert summary["preview"][-1] == ["r23c0", "r23c1", "r23c2"]


def test_preview_is_bounded_for_large_tables() -> None:
    summary = _table_summary(_asset(rows=200))
    assert summary["rows"] == 200
    assert len(summary["preview"]) == _PREVIEW_ROW_LIMIT


def test_headerless_table_keeps_all_rows() -> None:
    summary = _table_summary(_asset(rows=4, header=False))
    assert summary["rows"] == 4
    assert summary["header"] == []
    assert summary["preview"][0] == ["r0c0", "r0c1", "r0c2"]
