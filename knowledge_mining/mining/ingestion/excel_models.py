from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExcelWarning:
    code: str
    message: str
    sheet_name: str | None = None
    cell_range: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "code": self.code,
                "message": self.message,
                "sheet_name": self.sheet_name,
                "cell_range": self.cell_range,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class NormalizedSheet:
    name: str
    hidden: bool
    rows: tuple[tuple[str, ...], ...]
    formula_warnings: tuple[ExcelWarning, ...] = ()


@dataclass(frozen=True)
class NormalizedWorkbook:
    source_format: str
    sheets: tuple[NormalizedSheet, ...]
    warnings: tuple[ExcelWarning, ...] = ()


@dataclass(frozen=True)
class ExcelParseResult:
    markdown: str
    status: str
    source_format: str
    summary: dict[str, int]
    warnings: tuple[ExcelWarning, ...]
