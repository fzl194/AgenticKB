"""DOCX 原生解析适配器 + Normalizer（M3, SRS §C06/§C07, ADR-0003 D-028A）.

理念：全部用工业级成熟库（python-docx），自研代码只做"库输出 ->
BackendBlock"映射，不写任何解析算法。

- ``NativeDocxParser``：python-docx 从 BytesIO 打开；按 document body 顺序
  （``w:p`` / ``w:tbl`` 交替）产出 heading / paragraph / table 块。
  - heading 判级走库的 style API（style 名 ``Heading N`` / ``标题 N``），
    不猜字号；
  - 表格行列 + 合并单元格：遍历 ``tr.tc_lst`` 读 ``gridSpan`` /
    ``vMerge`` XML 属性（映射而非解析算法），横向合并产 column_span，
    纵向合并的 restart 格产 row_span、continue 格并入上方原点；
  - native_ref：heading/paragraph 带 ``{"paragraph_index": i}``（w:p 计数，
    空段也计数，保证索引稳定），table 带 ``{"table_index": t}``；
  - bbox 不伪造（None）。
- ``DocxNormalizer``：单一 section 容器 + heading 弹栈父链 +
  next_in_reading_order + EvidenceSpan(native_ref=paragraph_index,
  raw_text)。公共骨架见 ``_base.BaseNativeNormalizer``。

fingerprint：``native_docx@1.0.0#python-docx-<ver>``。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, replace
from importlib.metadata import version as _pkg_version
from typing import Any

from docx import Document as OpenDocx
from docx.oxml.ns import qn
from docx.table import Table as DocxTable, _Cell
from docx.text.paragraph import Paragraph

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

NATIVE_DOCX_PARSER_ID = "native_docx"
NATIVE_DOCX_VERSION = "1.0.0"
_PYTHON_DOCX_VERSION = _pkg_version("python-docx")
NATIVE_DOCX_FINGERPRINT = (
    f"{NATIVE_DOCX_PARSER_ID}@{NATIVE_DOCX_VERSION}"
    f"#python-docx-{_PYTHON_DOCX_VERSION}"
)

NATIVE_DOCX_MIMES = frozenset({
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})

# style 名判级：``Heading 1`` / ``heading 1`` / ``标题 1``（SRS §C06）。
_HEADING_STYLE_RE = re.compile(r"^(?:heading|标题)\s*(\d+)$", re.IGNORECASE)

DOCX_SECTION_CONTAINER_ID = "c-doc"


class NativeDocxParser:
    """DocumentParser 实现：python-docx 包装（SRS §C06）."""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=NATIVE_DOCX_PARSER_ID,
            display_name="Native DOCX Parser (python-docx)",
            version=NATIVE_DOCX_VERSION,
            supported_mimes=NATIVE_DOCX_MIMES,
            backend_kind="local",
            parser_fingerprint=NATIVE_DOCX_FINGERPRINT,
            capabilities=frozenset({"headings", "paragraphs", "tables", "merges"}),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{NATIVE_DOCX_PARSER_ID} cannot parse mime {mime!r}"
            )
        try:
            document = OpenDocx(io.BytesIO(data))
        except Exception as exc:  # 第三方异常不得穿越适配层（SRS §C06）
            raise ParserAdapterError(
                f"{NATIVE_DOCX_PARSER_ID}: python-docx failed to open source: {exc}"
            ) from exc

        blocks: list[BackendBlock] = []
        try:
            paragraph_index = 0
            table_index = 0
            for item in _iter_body(document):
                if isinstance(item, Paragraph):
                    block = _paragraph_block(item, paragraph_index)
                    paragraph_index += 1
                    if block is not None:
                        blocks.append(block)
                else:
                    blocks.append(_table_block(item, table_index))
                    table_index += 1
        except Exception as exc:  # 中段损坏也归一（§C06，评审 MED）
            raise ParserAdapterError(
                f"{NATIVE_DOCX_PARSER_ID}: failed to walk document body: {exc}"
            ) from exc

        return BackendParseArtifact(
            parser_id=NATIVE_DOCX_PARSER_ID,
            parser_version=NATIVE_DOCX_VERSION,
            mime=mime.lower(),
            blocks=tuple(blocks),
            warnings=(),
        )


# ---------------------------------------------------------------------------
# Parser 映射（模块级纯函数，便于单测）
# ---------------------------------------------------------------------------

def _iter_body(document):
    """按 document body XML 顺序产出 Paragraph / Table（保持阅读序）."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield DocxTable(child, document)


def _paragraph_block(paragraph: Paragraph, index: int) -> BackendBlock | None:
    """w:p -> heading/paragraph 块；空段落跳过但索引已计数."""
    text = paragraph.text.strip()
    if not text:
        return None
    style_name = paragraph.style.name if paragraph.style is not None else ""
    heading_match = _HEADING_STYLE_RE.match(style_name or "")
    if heading_match:
        return BackendBlock(
            block_type="heading",
            text=text,
            level=int(heading_match.group(1)),
            native_ref={"paragraph_index": index},
        )
    return BackendBlock(
        block_type="paragraph",
        text=text,
        native_ref={"paragraph_index": index},
    )


def _table_block(table: DocxTable, table_index: int) -> BackendBlock:
    """w:tbl -> table 块：structure 网格含 span（gridSpan/vMerge 直读）."""
    rows, cols = len(table.rows), len(table.columns)
    cells = _table_cells(table, rows, cols)
    structure: dict[str, Any] = {"rows": rows, "cols": cols, "cells": cells}
    return BackendBlock(
        block_type="table",
        text=_table_text(rows, cols, cells),
        native_ref={"table_index": table_index},
        structure=structure,
    )


@dataclass(frozen=True)
class _GridCell:
    """网格原点单元格（不可变；纵向合并的额外行数在收尾时回填）."""

    row_index: int
    column_index: int
    text: str
    column_span: int = 1
    extra_rows: int = 0  # vMerge continue 格数量

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "column_index": self.column_index,
            "text": self.text,
            "row_span": 1 + self.extra_rows,
            "column_span": self.column_span,
            "is_header": self.row_index == 0,  # 首行表头约定
        }


def _table_cells(
    table: DocxTable, rows: int, cols: int
) -> list[dict[str, Any]]:
    """tr.tc_lst + gridSpan/vMerge -> 展开的 cell 网格（仅原点产 cell）."""
    occupied = [[False] * cols for _ in range(rows)]
    # 列 -> (out 中纵向合并原点下标, 该原点已累计到的最后一行)。
    # 按行去重保证矩形合并（多列同源 vMerge）每行只计一次 extra_rows
    # （评审 HIGH-1：按列累计会把 2 列宽合并的 row_span 计成约 2 倍）。
    origins: dict[int, tuple[int, int]] = {}
    out: list[_GridCell] = []

    for r, row in enumerate(table.rows):
        c = 0
        counted_this_row: set[int] = set()  # 本行已累计过的合并原点
        for tc in row._tr.tc_lst:
            while c < cols and occupied[r][c]:
                c += 1
            if c >= cols:
                break
            col_span = int(tc.grid_span or 1)
            spanned = list(range(c, min(c + col_span, cols)))
            vmerge = tc.vMerge  # None | "restart" | "continue"
            if vmerge == "continue":
                for cc in spanned:
                    entry = origins.get(cc)
                    if entry is None:
                        continue
                    idx, _last = entry
                    # 同一行内同原点只累计一次（跨列矩形合并去重）
                    if idx not in counted_this_row:
                        counted_this_row.add(idx)
                        out[idx] = replace(
                            out[idx], extra_rows=out[idx].extra_rows + 1
                        )
                    origins[cc] = (idx, r)
            else:
                out.append(_GridCell(r, c, _Cell(tc, table).text.strip(), col_span))
                for cc in spanned:
                    if vmerge is not None:  # restart（含无 val 的 <w:vMerge/>）
                        origins[cc] = (len(out) - 1, r)
                    else:
                        origins.pop(cc, None)
            for cc in spanned:
                occupied[r][cc] = True
            c += col_span
    return [cell.as_dict() for cell in out]


def _table_text(
    rows: int, cols: int, cells: list[dict[str, Any]]
) -> str:
    """表格紧凑文本（\t 列分隔 / \n 行分隔），供 element.text 使用."""
    grid = [[""] * cols for _ in range(rows)]
    for cell in cells:
        grid[cell["row_index"]][cell["column_index"]] = str(cell["text"])
    return "\n".join("\t".join(row) for row in grid).strip()


# ---------------------------------------------------------------------------
# Normalizer（SRS §C07）
# ---------------------------------------------------------------------------

class DocxNormalizer(BaseNativeNormalizer):
    """DOCX backend artifact -> Parse IR：单 section + heading 父链."""

    normalizer_version = "native-docx@1"
    _default_fingerprints = {NATIVE_DOCX_PARSER_ID: NATIVE_DOCX_FINGERPRINT}
    _element_type_map = {"heading": "heading", "paragraph": "paragraph", "table": "table"}

    def _build_containers(self, artifact) -> tuple[Container, ...]:
        # DOCX 无页概念：单一文档级 section，page_number 不伪造（SRS §3.6）。
        return (Container(
            container_id=DOCX_SECTION_CONTAINER_ID,
            container_type="section",
            order_index=0,
            name="document",
        ),)

    def _make_spans(self, element_id, block, container_id) -> tuple[EvidenceSpan, ...]:
        return (EvidenceSpan(
            span_id=f"{element_id}-s0",
            native_ref=dict(block.native_ref) if block.native_ref else None,
            text_range=(0, len(block.text)),
            raw_text=block.text or None,
        ),)


__all__ = [
    "DOCX_SECTION_CONTAINER_ID",
    "NATIVE_DOCX_FINGERPRINT",
    "NATIVE_DOCX_MIMES",
    "NATIVE_DOCX_PARSER_ID",
    "NATIVE_DOCX_VERSION",
    "DocxNormalizer",
    "NativeDocxParser",
]
