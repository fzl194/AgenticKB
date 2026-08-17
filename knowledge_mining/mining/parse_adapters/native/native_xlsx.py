"""XLSX 原生解析适配器 + Normalizer（M3, SRS §C06/§C07, ADR-0003 D-028A）.

理念：全部用工业级成熟库（openpyxl），自研代码只做"库输出 ->
BackendBlock"映射。

- ``NativeXlsxParser``：标准双读模式——``data_only=False`` 拿公式、
  ``data_only=True`` 拿展示值（openpyxl 官方推荐做法）。workbook ->
  sheet 容器层级由 block 的 ``container_ref`` 表达；每个非空 sheet 产出
  一个 table 块：
  - 合并区域展开为原点格的 row_span/column_span（``merged_cells.ranges``
    直读），被覆盖位置不产 cell；
  - 公式格 ``formula`` 与展示文本 ``text`` 分离（无缓存值时 text 为空串，
    不伪造计算结果，SRS §7.4）；
  - 每格带 ``evidence_index``，Normalizer 据此把 TableCell.source_span_id
    关联到 cell 级 EvidenceSpan（native_ref={"sheet":..,"cell":"A1"}）。
- ``XlsxNormalizer``：workbook + sheet 容器层级（parent_container_id），
  sheet -> element(table) + TableAsset（首行 is_header 约定）。公共骨架见
  ``_base.BaseNativeNormalizer``。

fingerprint：``native_xlsx@1.0.0#openpyxl-<ver>``。
"""
from __future__ import annotations

import io
from importlib.metadata import version as _pkg_version
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from knowledge_mining.mining.contracts.parse_ir import (
    Container,
    EvidenceSpan,
)
from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
    ParserAdapterError,
    ParserDescriptor,
    UnsupportedFormat,
)
from knowledge_mining.mining.parse_adapters.native._base import (
    BaseNativeNormalizer,
)

NATIVE_XLSX_PARSER_ID = "native_xlsx"
NATIVE_XLSX_VERSION = "1.0.0"
_OPENPYXL_VERSION = _pkg_version("openpyxl")
NATIVE_XLSX_FINGERPRINT = (
    f"{NATIVE_XLSX_PARSER_ID}@{NATIVE_XLSX_VERSION}"
    f"#openpyxl-{_OPENPYXL_VERSION}"
)

NATIVE_XLSX_MIMES = frozenset({
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
})

WORKBOOK_CONTAINER_ID = "c-workbook"


class NativeXlsxParser:
    """DocumentParser 实现：openpyxl 双读包装（SRS §C06）."""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=NATIVE_XLSX_PARSER_ID,
            display_name="Native XLSX Parser (openpyxl)",
            version=NATIVE_XLSX_VERSION,
            supported_mimes=NATIVE_XLSX_MIMES,
            backend_kind="local",
            parser_fingerprint=NATIVE_XLSX_FINGERPRINT,
            capabilities=frozenset({"sheets", "tables", "merges", "formulas"}),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{NATIVE_XLSX_PARSER_ID} cannot parse mime {mime!r}"
            )
        try:
            wb_formula = load_workbook(io.BytesIO(data), data_only=False)
            wb_values = load_workbook(io.BytesIO(data), data_only=True)
        except Exception as exc:  # 第三方异常不得穿越适配层（SRS §C06）
            raise ParserAdapterError(
                f"{NATIVE_XLSX_PARSER_ID}: openpyxl failed to open source: {exc}"
            ) from exc

        blocks: list[BackendBlock] = []
        warnings: list[str] = []
        try:
            for sheet_name in wb_formula.sheetnames:
                sheet_formula = wb_formula[sheet_name]
                sheet_values = wb_values[sheet_name]
                block = _sheet_block(sheet_formula, sheet_values)
                if block is None:
                    warnings.append(f"sheet {sheet_name!r} is empty; skipped")
                    continue
                blocks.append(block)
        except Exception as exc:  # 中段损坏也归一（§C06，评审 MED）
            raise ParserAdapterError(
                f"{NATIVE_XLSX_PARSER_ID}: failed to walk sheets: {exc}"
            ) from exc

        return BackendParseArtifact(
            parser_id=NATIVE_XLSX_PARSER_ID,
            parser_version=NATIVE_XLSX_VERSION,
            mime=mime.lower(),
            blocks=tuple(blocks),
            warnings=tuple(warnings),
        )


# ---------------------------------------------------------------------------
# Parser 映射（模块级纯函数，便于单测）
# ---------------------------------------------------------------------------

#: 不可信声明几何上限（评审 HIGH-2）：单 sheet 遍历网格面积与合并区域
#: 面积的上限。超限的合并区域按未合并处理并计入 clamped_merges（§7.4
#: 可见性）；超限网格截断为 (MAX_GRID_EDGE, MAX_GRID_EDGE)。真实文档
#: 远低于此量级；恶意 XLSX（如 mergeCell A1:XFD1048576）不再撑爆内存。
_MAX_GRID_AREA = 2_000_000  # rows*cols
_MAX_GRID_EDGE = 10_000
_MAX_MERGE_AREA = 100_000


def _sheet_block(
    sheet_formula: Worksheet, sheet_values: Worksheet
) -> BackendBlock | None:
    """一个 sheet -> 一个 table 块；空 sheet 返回 None."""
    rows = min(sheet_formula.max_row or 0, _MAX_GRID_EDGE)
    cols = min(sheet_formula.max_column or 0, _MAX_GRID_EDGE)
    if rows == 0 or cols == 0:
        return None
    grid_clamped = (
        rows * cols > _MAX_GRID_AREA
        or rows != (sheet_formula.max_row or 0)
        or cols != (sheet_formula.max_column or 0)
    )

    # 合并区域：原点 -> (row_span, col_span)；其余位置计入 covered。
    # 超面积上限的区域跳过展开（按未合并处理），计 clamped_merges。
    origin_spans: dict[tuple[int, int], tuple[int, int]] = {}
    covered: set[tuple[int, int]] = set()
    clamped_merges = 0
    for merged in sheet_formula.merged_cells.ranges:
        area = (merged.max_row - merged.min_row + 1) * (
            merged.max_col - merged.min_col + 1
        )
        if area > _MAX_MERGE_AREA:
            clamped_merges += 1
            continue
        top_left = (merged.min_row - 1, merged.min_col - 1)
        origin_spans[top_left] = (
            merged.max_row - merged.min_row + 1,
            merged.max_col - merged.min_col + 1,
        )
        for r in range(merged.min_row - 1, merged.max_row):
            for c in range(merged.min_col - 1, merged.max_col):
                covered.add((r, c))

    cells: list[dict[str, Any]] = []
    evidence = 0
    empty = True
    for r in range(rows):
        for c in range(cols):
            pos = (r, c)
            if pos in covered and pos not in origin_spans:
                continue
            cell = sheet_formula.cell(row=r + 1, column=c + 1)
            formula = _formula_of(cell.value)
            display = sheet_values.cell(row=r + 1, column=c + 1).value
            if formula is None and display is None and pos not in origin_spans:
                continue
            empty = False
            row_span, col_span = origin_spans.get(pos, (1, 1))
            cells.append({
                "row_index": r,
                "column_index": c,
                "text": "" if display is None else str(display),
                "row_span": row_span,
                "column_span": col_span,
                "is_header": r == 0,  # 首行表头约定
                "formula": formula,
                "evidence_index": evidence,
            })
            evidence += 1
    if empty:
        return None

    structure = {"rows": rows, "cols": cols, "cells": cells}
    if clamped_merges or grid_clamped:
        # 不可信声明几何被截断（§7.4 可见性，评审 HIGH-2）
        structure["clamped_geometry"] = {
            "merges": clamped_merges,
            "grid": bool(grid_clamped),
        }
    return BackendBlock(
        block_type="table",
        text="",
        container_ref={"container_type": "sheet", "name": sheet_formula.title},
        native_ref={"sheet": sheet_formula.title},
        structure=structure,
    )


def _formula_of(value: Any) -> str | None:
    """公式判定：openpyxl 中公式格的 value 是 "=..." 字符串."""
    if isinstance(value, str) and value.startswith("="):
        return value
    return None


# ---------------------------------------------------------------------------
# Normalizer（SRS §C07）
# ---------------------------------------------------------------------------

class XlsxNormalizer(BaseNativeNormalizer):
    """XLSX backend artifact -> Parse IR：workbook/sheet 层级 + cell 证据."""

    normalizer_version = "native-xlsx@1"
    _default_fingerprints = {NATIVE_XLSX_PARSER_ID: NATIVE_XLSX_FINGERPRINT}
    _element_type_map = {"table": "table"}

    def _build_containers(self, artifact) -> tuple[Container, ...]:
        containers = [Container(
            container_id=WORKBOOK_CONTAINER_ID,
            container_type="workbook",
            order_index=0,
            name="workbook",
        )]
        sheet_names = [
            (b.container_ref or {}).get("name")
            for b in artifact.blocks if b.container_ref
        ]
        for order, name in enumerate(sheet_names, start=1):
            containers.append(Container(
                container_id=f"c-sheet-{order - 1}",
                container_type="sheet",
                order_index=order,
                name=name,
                parent_container_id=WORKBOOK_CONTAINER_ID,
            ))
        return tuple(containers)

    def _container_id_for(self, block, containers) -> str | None:
        ref = block.container_ref or {}
        name = ref.get("name")
        for container in containers:
            if container.container_type == "sheet" and container.name == name:
                return container.container_id
        return None

    def _make_spans(self, element_id, block, container_id) -> tuple[EvidenceSpan, ...]:
        """每个 cell 一个 EvidenceSpan（native_ref=sheet+cell 坐标）."""
        structure = block.structure or {}
        raw_cells = structure.get("cells") or []
        sheet_name = (block.native_ref or {}).get("sheet")
        spans: list[EvidenceSpan] = []
        for k, cell in enumerate(raw_cells):
            coord = _cell_coord(cell["row_index"], cell["column_index"])
            spans.append(EvidenceSpan(
                span_id=f"{element_id}-cell-{k:04d}",
                native_ref={"sheet": sheet_name, "cell": coord},
                raw_text=str(cell.get("text", "")) or None,
            ))
        return tuple(spans)


def _cell_coord(row_index: int, column_index: int) -> str:
    """0-based (row, col) -> A1 坐标（列字母 + 1-based 行号）."""
    col, letters = column_index, ""
    while True:
        col, rem = divmod(col, 26)
        letters = chr(ord("A") + rem) + letters
        if col == 0:
            break
        col -= 1
    return f"{letters}{row_index + 1}"


__all__ = [
    "NATIVE_XLSX_FINGERPRINT",
    "NATIVE_XLSX_MIMES",
    "NATIVE_XLSX_PARSER_ID",
    "NATIVE_XLSX_VERSION",
    "NativeXlsxParser",
    "WORKBOOK_CONTAINER_ID",
    "XlsxNormalizer",
]
