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
NATIVE_DOCX_VERSION = "2.0.0"
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
            capabilities=frozenset({
                "headings", "paragraphs", "lists", "tables", "merges",
            }),
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
            warnings = _diagnose_unsupported_structures(document)
            paragraph_index = 0
            table_index = 0
            for item in _iter_body(document):
                if isinstance(item, Paragraph):
                    block = _paragraph_block(item, paragraph_index)
                    paragraph_index += 1
                    if block is not None:
                        blocks.append(block)
                else:
                    blocks.extend(_table_blocks(item, table_index))
                    table_index += 1 + _count_nested_tables(item)
        except Exception as exc:  # 中段损坏也归一（§C06，评审 MED）
            raise ParserAdapterError(
                f"{NATIVE_DOCX_PARSER_ID}: failed to walk document body: {exc}"
            ) from exc

        return BackendParseArtifact(
            parser_id=NATIVE_DOCX_PARSER_ID,
            parser_version=NATIVE_DOCX_VERSION,
            mime=mime.lower(),
            blocks=tuple(blocks),
            warnings=tuple(warnings),
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
    """w:p -> heading/list_item/paragraph 块；空段落跳过但索引已计数.

    列表语义（整改轮）：``w:numPr``（编号/项目符号属性）是 OOXML 的
    列表成员声明——映射为 ``list_item``，``level = w:ilvl + 1``（0 基
    缩进层级 -> 1 基元素层级）。无 numPr 的 "List *" 样式段落仍是普通
    段落（样式不等于列表成员资格）。
    """
    text = paragraph.text.strip()
    if not text:
        return None
    num_level = _list_level_of(paragraph)
    if num_level is not None:
        return BackendBlock(
            block_type="list_item",
            text=text,
            level=num_level,
            native_ref={"paragraph_index": index},
        )
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


def _list_level_of(paragraph: Paragraph) -> int | None:
    """w:numPr -> 列表层级（ilvl+1）；无 numPr 返回 None（非列表成员）."""
    pPr = paragraph._p.pPr
    if pPr is None:
        return None
    numPr = pPr.find(qn("w:numPr"))
    if numPr is None:
        return None
    ilvl = numPr.find(qn("w:ilvl"))
    try:
        level = int(ilvl.get(qn("w:val"))) if ilvl is not None else 0
    except (TypeError, ValueError):
        level = 0
    return level + 1


def _table_blocks(table: DocxTable, table_index: int) -> list[BackendBlock]:
    """w:tbl -> [父表块, 嵌套表块...]（嵌套表独立成块，整改轮）.

    嵌套表经 **XML 层**遍历（``w:tbl//w:tbl``）发现——lxml 元素代理的
    Python ``id()`` 不稳定（代理可被回收重建），按 id 去重会在跨测试
    组合下偶发漏检。宿主格以 ``in_cell`` 标记（宿主表索引）。
    """
    out = [_table_block(table, table_index)]
    nested_index = table_index + 1
    for tbl_el in table._tbl.iter(qn("w:tbl")):  # noqa: SLF001
        if tbl_el is table._tbl:  # noqa: SLF001
            continue
        out.append(_table_block(
            DocxTable(tbl_el, table._parent),  # noqa: SLF001
            nested_index,
            in_cell_table=table_index,
        ))
        nested_index += 1
    return out


def _count_nested_tables(table: DocxTable) -> int:
    return sum(
        1 for tbl_el in table._tbl.iter(qn("w:tbl"))  # noqa: SLF001
        if tbl_el is not table._tbl  # noqa: SLF001
    )


def _table_block(
    table: DocxTable,
    table_index: int,
    in_cell_table: int | None = None,
) -> BackendBlock:
    """w:tbl -> table 块：structure 网格含 span（gridSpan/vMerge 直读）."""
    rows, cols = len(table.rows), len(table.columns)
    cells = _table_cells(table, rows, cols)
    structure: dict[str, Any] = {"rows": rows, "cols": cols, "cells": cells}
    native_ref: dict[str, Any] = {"table_index": table_index}
    if in_cell_table is not None:
        # 嵌套表：宿主表索引即 OOXML 定位（宿主格坐标由网格合并语义
        # 推导不可靠，不伪造）。
        native_ref["in_cell"] = in_cell_table
    return BackendBlock(
        block_type="table",
        text=_table_text(rows, cols, cells),
        native_ref=native_ref,
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
                out.append(_GridCell(r, c, _cell_text_excluding_nested(tc), col_span))
                for cc in spanned:
                    if vmerge is not None:  # restart（含无 val 的 <w:vMerge/>）
                        origins[cc] = (len(out) - 1, r)
                    else:
                        origins.pop(cc, None)
            for cc in spanned:
                occupied[r][cc] = True
            c += col_span
    cells = [cell.as_dict() for cell in out]
    for k, cell in enumerate(cells):
        cell["evidence_index"] = k  # cell 级证据索引（不变量 I-4）
    return cells


def _cell_text_excluding_nested(tc: Any) -> str:
    """单元格直属段落文本（嵌套表由独立块表达，不并入宿主格文本）."""
    parts: list[str] = []
    for child in tc.iterchildren():
        if child.tag != qn("w:p"):
            continue
        text = "".join(
            t.text or ""
            for t in child.iter() if t.tag == qn("w:t")
        ).strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# 未支持结构诊断（整改轮：不静默丢失，SRS §7.4）
# ---------------------------------------------------------------------------

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _count_localname(root: Any, local: str) -> int:
    return sum(
        1 for el in root.iter()
        if isinstance(el.tag, str) and el.tag.endswith("}" + local)
    )


def _related_part_count(document: Any, suffix: str, skip_ids=frozenset({-1, 0})) -> tuple[int, int]:
    """按 reltype 后缀找 notes part -> (元素数, 非分隔符数)."""
    for rel in document.part.rels.values():
        if not rel.reltype.endswith(suffix):
            continue
        try:
            from lxml import etree

            root = etree.fromstring(rel.target_part.blob)
        except Exception:  # noqa: BLE001 —— part 解析失败按 0 处理
            return 0, 0
        elements = [
            el for el in root.iter()
            if isinstance(el.tag, str) and el.tag.endswith("}footnote")
        ]
        real = [
            el for el in elements
            if _int_attr(el, "id", default=-99) not in skip_ids
        ]
        return len(elements), len(real)
    return 0, 0


def _int_attr(el: Any, name: str, default: int = -99) -> int:
    raw = el.get(f"{_W_NS}{name}")
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _diagnose_unsupported_structures(document: Any) -> list[str]:
    """图片/页眉页脚/脚注尾注/批注/文本框计数诊断（不静默丢失）."""
    warnings: list[str] = []
    body = document.element.body

    images = _count_localname(body, "blip")
    if images:
        warnings.append(
            f"document contains {images} image(s); image extraction not "
            "supported yet (diagnosed, not parsed)"
        )
    textboxes = _count_localname(body, "txbxContent")
    if textboxes:
        warnings.append(
            f"document contains {textboxes} textbox(es); "
            "textbox extraction not supported yet (diagnosed, not parsed)"
        )

    header_texts = 0
    footer_texts = 0
    for section in document.sections:
        for hf in (
            section.header, section.footer,
            section.even_page_header, section.even_page_footer,
            section.first_page_header, section.first_page_footer,
        ):
            try:
                has_text = any(
                    (p.text or "").strip() for p in hf.paragraphs
                )
            except Exception:  # noqa: BLE001 —— 缺 part 的节按无内容处理
                continue
            if not has_text:
                continue
            if "header" in type(hf).__name__.lower():
                header_texts += 1
            else:
                footer_texts += 1
    if header_texts:
        warnings.append(
            f"document contains {header_texts} header part(s) with text; "
            "header/footer extraction not supported yet (diagnosed, not parsed)"
        )
    if footer_texts:
        warnings.append(
            f"document contains {footer_texts} footer part(s) with text; "
            "footer extraction not supported yet (diagnosed, not parsed)"
        )

    _, real_footnotes = _related_part_count(document, "/footnotes")
    if real_footnotes:
        warnings.append(
            f"document contains {real_footnotes} footnote(s); footnote "
            "extraction not supported yet (diagnosed, not parsed)"
        )
    comment_count = _related_comment_count(document)
    if comment_count:
        warnings.append(
            f"document contains {comment_count} comment(s); comment "
            "extraction not supported yet (diagnosed, not parsed)"
        )
    return warnings


def _related_comment_count(document: Any) -> int:
    for rel in document.part.rels.values():
        if rel.reltype.endswith("/comments"):
            try:
                from lxml import etree

                root = etree.fromstring(rel.target_part.blob)
                return _count_localname(root, "comment")
            except Exception:  # noqa: BLE001
                return 0
    return 0


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

    normalizer_version = "native-docx@2"
    _default_fingerprints = {NATIVE_DOCX_PARSER_ID: NATIVE_DOCX_FINGERPRINT}
    _element_type_map = {
        "heading": "heading",
        "paragraph": "paragraph",
        "list_item": "list_item",
        "table": "table",
    }

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

    def _make_cell_spans(
        self, element_id, block, container_id
    ) -> tuple[EvidenceSpan, ...]:
        """表格 cell 级 EvidenceSpan（OOXML table/row/col 定位，I-4）."""
        if block.block_type != "table":
            return ()
        structure = block.structure or {}
        raw_cells = structure.get("cells") or []
        native = block.native_ref or {}
        spans: list[EvidenceSpan] = []
        for k, cell in enumerate(raw_cells):
            ref: dict[str, Any] = {
                "table_index": native.get("table_index"),
                "row_index": int(cell["row_index"]),
                "column_index": int(cell["column_index"]),
            }
            if "in_cell" in native:
                ref["in_cell"] = native["in_cell"]
            spans.append(EvidenceSpan(
                span_id=f"{element_id}-cell-{k:04d}",
                native_ref=ref,
                raw_text=str(cell.get("text", "")) or None,
            ))
        return tuple(spans)


__all__ = [
    "DOCX_SECTION_CONTAINER_ID",
    "NATIVE_DOCX_FINGERPRINT",
    "NATIVE_DOCX_MIMES",
    "NATIVE_DOCX_PARSER_ID",
    "NATIVE_DOCX_VERSION",
    "DocxNormalizer",
    "NativeDocxParser",
]
