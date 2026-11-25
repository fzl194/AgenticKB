# -*- coding: utf-8 -*-
"""CSV 原生解析适配器（S2 文件格式支持补全，68号报告 §六.S2）.

理念与 onenet_jsonl 一致：parser 只产 BackendBlock（heading/paragraph/table
三类），IR 交给 ``LegacyLineNormalizer``（其 structure={"columns","rows"}
表结构路径已由 markdown/onenet 两条链验证）。

- 整个文件视为**一张语义表**：首行=表头（与 markdown 管道表同约定）；
- 编码：utf-8 → utf-8-sig（BOM）→ gbk 回退（中文 Excel 导出常态）；
- 定界符：``csv.Sniffer`` 嗅探（失败回退逗号）；
- 几何上限（防轰炸）：行/列截断 + 诊断 warning（不静默）；
- 空文件（无数据行）：零块 + warning（挖掘侧 empty_document 质量门
  会如实判 FAIL，不伪造空表）。

fingerprint：``native_csv@1.0.0#csv-stdlib``。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
    ParserAdapterError,
    ParserDescriptor,
    UnsupportedFormat,
)

NATIVE_CSV_PARSER_ID = "native_csv"
NATIVE_CSV_VERSION = "1.0.0"
NATIVE_CSV_FINGERPRINT = f"{NATIVE_CSV_PARSER_ID}@{NATIVE_CSV_VERSION}#csv-stdlib"

NATIVE_CSV_MIMES = frozenset({"text/csv"})

#: 几何上限（与 xlsx 适配器的防轰炸策略同思路）。
_MAX_ROWS = 50_000
_MAX_COLS = 256


def _decode(data: bytes) -> str:
    """utf-8(-sig) → gbk 回退；都失败才是真损坏（ParserAdapterError）。"""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParserAdapterError(
        f"{NATIVE_CSV_PARSER_ID}: cannot decode bytes as utf-8/gbk"
    )


def _sniff_dialect(text: str) -> csv.Dialect | None:
    """嗅探定界符（只看头部；失败返回 None → 逗号）。"""
    sample = "\n".join(text.splitlines()[:32])
    if not sample:
        return None
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return None


class NativeCsvParser:
    """DocumentParser 实现：csv stdlib → 单 table block。"""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=NATIVE_CSV_PARSER_ID,
            display_name="Native CSV Parser (csv stdlib)",
            version=NATIVE_CSV_VERSION,
            supported_mimes=NATIVE_CSV_MIMES,
            backend_kind="local",
            parser_fingerprint=NATIVE_CSV_FINGERPRINT,
            capabilities=frozenset({"tables", "rows"}),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{NATIVE_CSV_PARSER_ID} cannot parse mime {mime!r}"
            )
        text = _decode(data)
        dialect = _sniff_dialect(text)
        warnings: list[str] = []
        try:
            if dialect is not None:
                grid = list(csv.reader(io.StringIO(text), dialect))
            else:
                grid = list(csv.reader(io.StringIO(text)))
        except csv.Error as exc:
            raise ParserAdapterError(
                f"{NATIVE_CSV_PARSER_ID}: csv parse failed: {exc}"
            ) from exc

        # 去空行
        grid = [row for row in grid if any((c or "").strip() for c in row)]
        if not grid:
            warnings.append("empty csv: no data rows")
            return BackendParseArtifact(
                parser_id=NATIVE_CSV_PARSER_ID,
                parser_version=NATIVE_CSV_VERSION,
                mime=mime.lower(),
                blocks=(),
                warnings=tuple(warnings),
            )

        max_cols = max(len(row) for row in grid)
        if max_cols > _MAX_COLS:
            warnings.append(
                f"csv columns truncated to {_MAX_COLS} (found {max_cols})"
            )
            grid = [row[:_MAX_COLS] for row in grid]
        if len(grid) - 1 > _MAX_ROWS:
            warnings.append(
                f"csv rows truncated to {_MAX_ROWS} (found {len(grid) - 1})"
            )
            grid = grid[: _MAX_ROWS + 1]

        columns = [str(c) for c in grid[0]] or [f"col{i}" for i in range(max_cols)]
        # 列名去重/补齐（同名列后者覆盖语义不可靠，编号消歧）
        seen: dict[str, int] = {}
        unique_columns: list[str] = []
        for i, col in enumerate(columns):
            if col == "" or col in seen:
                seen[col] = seen.get(col, 0) + 1
                unique_columns.append(f"{col or 'col'}_{i}")
            else:
                seen[col] = 0
                unique_columns.append(col)

        rows: list[dict[str, Any]] = []
        for raw in grid[1:]:
            rows.append({
                unique_columns[i]: (raw[i] if i < len(raw) else "")
                for i in range(len(unique_columns))
            })

        return BackendParseArtifact(
            parser_id=NATIVE_CSV_PARSER_ID,
            parser_version=NATIVE_CSV_VERSION,
            mime=mime.lower(),
            blocks=(
                BackendBlock(
                    block_type="table",
                    text="",
                    structure={"columns": unique_columns, "rows": rows},
                    native_ref={"kind": "csv_file"},
                ),
            ),
            warnings=tuple(warnings),
        )


__all__ = [
    "NATIVE_CSV_FINGERPRINT", "NATIVE_CSV_MIMES", "NATIVE_CSV_PARSER_ID",
    "NATIVE_CSV_VERSION", "NativeCsvParser",
]
