from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ExcelRegion:
    top: int
    left: int
    bottom: int
    right: int
    rows: tuple[tuple[str, ...], ...]

    @property
    def a1_range(self) -> str:
        return (
            f"{_column_name(self.left)}{self.top + 1}:"
            f"{_column_name(self.right - 1)}{self.bottom}"
        )


def _column_name(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _present(value: str) -> bool:
    return bool(value.strip())


def _bands(start: int, end: int, separators: set[int]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    cursor = start
    for position in sorted(separators):
        if cursor < position:
            result.append((cursor, position))
        cursor = position + 1
    if cursor < end:
        result.append((cursor, end))
    return result


def detect_regions(rows: tuple[tuple[str, ...], ...]) -> tuple[ExcelRegion, ...]:
    if not rows:
        return ()
    width = max((len(row) for row in rows), default=0)
    matrix = tuple(
        tuple(row[column] if column < len(row) else "" for column in range(width))
        for row in rows
    )
    occupied = [
        (row_index, column_index)
        for row_index, row in enumerate(matrix)
        for column_index, value in enumerate(row)
        if _present(value)
    ]
    if not occupied:
        return ()

    top = min(item[0] for item in occupied)
    bottom = max(item[0] for item in occupied) + 1
    left = min(item[1] for item in occupied)
    right = max(item[1] for item in occupied) + 1
    found: list[ExcelRegion] = []

    def split(region_top: int, region_left: int, region_bottom: int, region_right: int) -> None:
        empty_rows = {
            row_index
            for row_index in range(region_top, region_bottom)
            if not any(
                _present(matrix[row_index][column_index])
                for column_index in range(region_left, region_right)
            )
        }
        if empty_rows:
            for band_top, band_bottom in _bands(
                region_top, region_bottom, empty_rows
            ):
                split(band_top, region_left, band_bottom, region_right)
            return

        empty_columns = {
            column_index
            for column_index in range(region_left, region_right)
            if not any(
                _present(matrix[row_index][column_index])
                for row_index in range(region_top, region_bottom)
            )
        }
        if empty_columns:
            for band_left, band_right in _bands(
                region_left, region_right, empty_columns
            ):
                split(region_top, band_left, region_bottom, band_right)
            return

        region_rows = tuple(
            tuple(
                matrix[row_index][column_index]
                for column_index in range(region_left, region_right)
            )
            for row_index in range(region_top, region_bottom)
        )
        found.append(
            ExcelRegion(
                region_top,
                region_left,
                region_bottom,
                region_right,
                region_rows,
            )
        )

    split(top, left, bottom, right)
    return tuple(sorted(found, key=lambda item: (item.top, item.left)))


_SCALAR_RE = re.compile(
    r"^(?:[-+]?\d+(?:\.\d+)?%?|true|false|"
    r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?)?|"
    r"#[A-Z0-9/?!]+)$",
    re.IGNORECASE,
)


def _looks_like_header(row: tuple[str, ...]) -> bool:
    values = [value.strip() for value in row if value.strip()]
    return (
        bool(values)
        and len(values) * 2 >= len(row)
        and not any(_SCALAR_RE.fullmatch(value) for value in values)
    )


def _generated_headers(width: int) -> tuple[str, ...]:
    return tuple(f"列{_column_name(index)}" for index in range(width))


def infer_headers(
    rows: tuple[tuple[str, ...], ...], *, max_header_rows: int = 3
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    width = max((len(row) for row in rows), default=0)
    if len(rows) < 3 or not _looks_like_header(rows[0]):
        return _generated_headers(width), rows

    depth = 1
    while depth < min(max_header_rows, len(rows) - 1):
        previous = [value for value in rows[depth - 1] if value]
        needs_child_header = (
            len(previous) != len(set(previous))
            or any(not value for value in rows[depth - 1])
        )
        if not needs_child_header or not _looks_like_header(rows[depth]):
            break
        depth += 1

    headers: list[str] = []
    for column_index in range(width):
        parts: list[str] = []
        for row in rows[:depth]:
            value = row[column_index].strip() if column_index < len(row) else ""
            if value and (not parts or parts[-1] != value):
                parts.append(value)
        headers.append(
            "/".join(parts) if parts else f"列{_column_name(column_index)}"
        )

    seen: dict[str, int] = {}
    unique: list[str] = []
    for header in headers:
        seen[header] = seen.get(header, 0) + 1
        unique.append(header if seen[header] == 1 else f"{header}_{seen[header]}")
    return tuple(unique), rows[depth:]
