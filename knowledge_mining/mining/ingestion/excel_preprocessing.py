from __future__ import annotations

from pathlib import Path

from knowledge_mining.mining.infra.excel_config import ExcelConfig
from knowledge_mining.mining.infra.text_utils import token_count
from knowledge_mining.mining.ingestion.errors import PreprocessingError
from knowledge_mining.mining.ingestion.excel_models import ExcelParseResult
from knowledge_mining.mining.ingestion.excel_reader import read_excel_workbook
from knowledge_mining.mining.ingestion.excel_structure import (
    ExcelRegion,
    detect_regions,
    infer_headers,
)


def excel_to_markdown(
    path: Path,
    config: ExcelConfig | None = None,
) -> ExcelParseResult:
    path = Path(path)
    cfg = config or ExcelConfig()
    workbook = read_excel_workbook(path)
    if len(workbook.sheets) > cfg.max_sheets:
        raise PreprocessingError(
            "excel_limits_exceeded", "Excel workbook has too many sheets"
        )

    warnings = list(workbook.warnings)
    sections = [f"# {_escape_heading(path.name)}"]
    parsed_sheet_count = 0
    skipped_empty_sheet_count = 0
    table_region_count = 0
    nonempty_cell_count = 0

    for sheet in workbook.sheets:
        warnings.extend(sheet.formula_warnings)
        sheet_cells = sum(bool(value) for row in sheet.rows for value in row)
        nonempty_cell_count += sheet_cells
        if nonempty_cell_count > cfg.max_nonempty_cells:
            raise PreprocessingError(
                "excel_limits_exceeded",
                "Excel workbook has too many nonempty cells",
            )
        if sheet_cells == 0:
            skipped_empty_sheet_count += 1
            continue

        parsed_sheet_count += 1
        suffix = "（隐藏）" if sheet.hidden else ""
        sections.append(f"## 工作表：{_escape_heading(sheet.name)}{suffix}")
        for region in detect_regions(sheet.rows):
            table_region_count += 1
            sections.extend(
                _render_region(region, cfg.table_chunk_target_tokens)
            )

    if parsed_sheet_count == 0:
        raise PreprocessingError(
            "excel_no_usable_content", "Excel workbook has no usable cells"
        )

    summary = {
        "sheet_count": len(workbook.sheets),
        "parsed_sheet_count": parsed_sheet_count,
        "skipped_empty_sheet_count": skipped_empty_sheet_count,
        "table_region_count": table_region_count,
        "nonempty_cell_count": nonempty_cell_count,
    }
    return ExcelParseResult(
        markdown="\n\n".join(sections).strip() + "\n",
        status="partial" if warnings else "success",
        source_format=workbook.source_format,
        summary=summary,
        warnings=tuple(warnings),
    )


def _escape_heading(value: str) -> str:
    return " ".join(value.replace("\r", "\n").splitlines()).strip()


def _escape_cell(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


def _table_lines(
    headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]
) -> list[str]:
    width = len(headers)
    lines = [
        "| " + " | ".join(_escape_cell(value) for value in headers) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    for row in rows:
        padded = tuple(row[index] if index < len(row) else "" for index in range(width))
        lines.append(
            "| " + " | ".join(_escape_cell(value) for value in padded) + " |"
        )
    return lines


def _chunk_rows(
    headers: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    target_tokens: int,
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    if not rows:
        return ((),)

    chunks: list[tuple[tuple[str, ...], ...]] = []
    current: list[tuple[str, ...]] = []
    for row in rows:
        candidate = tuple([*current, row])
        candidate_text = "\n".join(_table_lines(headers, candidate))
        if current and token_count(candidate_text) > target_tokens:
            chunks.append(tuple(current))
            current = [row]
        else:
            current.append(row)
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def _render_region(region: ExcelRegion, target_tokens: int) -> list[str]:
    if not region.rows:
        return []
    width = max((len(row) for row in region.rows), default=0)
    if width == 1:
        items = [
            f"- {_escape_cell(row[0])}"
            for row in region.rows
            if row and row[0]
        ]
        return [f"### 内容 {region.a1_range}", "\n".join(items)]

    headers, data_rows = infer_headers(region.rows)
    chunks = _chunk_rows(headers, data_rows, target_tokens)
    rendered = [f"### 表格 {region.a1_range}"]
    rendered.extend("\n".join(_table_lines(headers, chunk)) for chunk in chunks)
    return rendered
