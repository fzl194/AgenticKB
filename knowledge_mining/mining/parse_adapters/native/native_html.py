"""HTML 原生解析适配器 + Normalizer（M3, SRS §C06/§C07, ADR-0003 D-028A）.

理念：全部用工业级成熟库（lxml），自研代码只做"DOM -> BackendBlock"
映射（遍历与属性直读），不写解析算法。

- ``NativeHtmlParser``：``etree.fromstring(bytes, HTMLParser())``（encoding
  声明由库处理）。文档序遍历 DOM：
  - ``<title>`` -> title 块；``h1``-``h6`` -> heading(level)；
    ``p`` -> paragraph（inline 标签文本收拢）；``li`` -> list_item
    （level=列表嵌套深度）；``pre``/``code`` -> code；
    ``img`` -> figure（alt/src 进 structure）；``table`` -> table
    （``rowspan``/``colspan`` 属性直读展开，被覆盖位置不产 cell）；
  - native_ref = {"xpath": getpath(节点)}；
  - block 元素不再向下递归（内容已收拢），容器元素继续深度优先。
- ``HtmlNormalizer``：单一 dom_document 容器 + heading 弹栈父链 +
  阅读序 + TableAsset。公共骨架见 ``_base.BaseNativeNormalizer``。

fingerprint：``native_html@1.0.0#lxml-<ver>``。
"""
from __future__ import annotations

import re
from importlib.metadata import version as _pkg_version
from typing import Any

from lxml import etree

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

NATIVE_HTML_PARSER_ID = "native_html"
NATIVE_HTML_VERSION = "1.0.0"
_LXML_VERSION = _pkg_version("lxml")
NATIVE_HTML_FINGERPRINT = (
    f"{NATIVE_HTML_PARSER_ID}@{NATIVE_HTML_VERSION}#lxml-{_LXML_VERSION}"
)

NATIVE_HTML_MIMES = frozenset({"text/html", "application/xhtml+xml"})

DOM_CONTAINER_ID = "c-dom"

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_LIST_TAGS = frozenset({"ul", "ol"})
_LIST_ROOT_TAGS = _LIST_TAGS | {"dl"}
# 命中即产块且不再向下递归的标签。
_BLOCK_TAGS = frozenset(_HEADING_TAGS) | {
    "p", "li", "pre", "code", "table", "img", "title",
}

# charset 声明嗅探（仅用于决定是否强制 UTF-8，解码本身仍由库完成）。
_HAS_ENCODING_DECL_RE = re.compile(
    rb"""(?:<meta[^>]+charset|<\?xml[^>]+encoding)""", re.IGNORECASE
)


class NativeHtmlParser:
    """DocumentParser 实现：lxml DOM 遍历包装（SRS §C06）."""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=NATIVE_HTML_PARSER_ID,
            display_name="Native HTML Parser (lxml)",
            version=NATIVE_HTML_VERSION,
            supported_mimes=NATIVE_HTML_MIMES,
            backend_kind="local",
            parser_fingerprint=NATIVE_HTML_FINGERPRINT,
            capabilities=frozenset({
                "headings", "paragraphs", "lists", "tables", "figures",
            }),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{NATIVE_HTML_PARSER_ID} cannot parse mime {mime!r}"
            )
        root = _parse_dom(data)
        tree = root.getroottree()
        blocks: list[BackendBlock] = []
        _walk(root, tree, list_depth=0, blocks=blocks)
        return BackendParseArtifact(
            parser_id=NATIVE_HTML_PARSER_ID,
            parser_version=NATIVE_HTML_VERSION,
            mime=mime.lower(),
            blocks=tuple(blocks),
        )


def _parse_dom(data: bytes) -> Any:
    """bytes -> lxml root；失败包适配层错误（契约 v1.1 invalid_encoding）.

    编码策略：源码带 charset 声明（meta / XML 声明）时交给库嗅探；
    无声明时按项目 UTF-8 约定强制解码（lxml 默认会回退 latin-1 导致
    中文 mojibake），坏字节包 :class:`ParserAdapterError`。
    """
    parser = (
        etree.HTMLParser()
        if _HAS_ENCODING_DECL_RE.search(data[:4096])
        else etree.HTMLParser(encoding="utf-8")
    )
    try:
        root = etree.fromstring(data, parser)
    except Exception as exc:  # 第三方异常不得穿越适配层（SRS §C06）
        raise ParserAdapterError(
            f"{NATIVE_HTML_PARSER_ID}: lxml failed to parse source: {exc}"
        ) from exc
    if root is None:
        raise ParserAdapterError(
            f"{NATIVE_HTML_PARSER_ID}: lxml produced no DOM for source"
        )
    return root


# ---------------------------------------------------------------------------
# Parser 映射（模块级纯函数，便于单测）
# ---------------------------------------------------------------------------

def _walk(
    element: Any, tree: Any, *, list_depth: int, blocks: list[BackendBlock]
) -> None:
    """文档序深度优先：命中块标签产块；容器标签继续递归."""
    tag = element.tag
    if not isinstance(tag, str):  # 注释 / PI 等无 tag 节点
        return
    if tag in _BLOCK_TAGS:
        block = _element_block(element, tree, list_depth)
        if block is not None:
            blocks.append(block)
        return
    child_depth = (
        list_depth + 1 if tag in _LIST_ROOT_TAGS else list_depth
    )
    for child in element.iterchildren():
        _walk(child, tree, list_depth=child_depth, blocks=blocks)


def _element_block(
    element: Any, tree: Any, list_depth: int
) -> BackendBlock | None:
    """单个块级元素 -> BackendBlock（native_ref=xpath）."""
    tag = element.tag
    native_ref = {"xpath": tree.getpath(element)}
    if tag in _HEADING_TAGS:
        return BackendBlock(
            block_type="heading",
            text=_text_of(element),
            level=_HEADING_TAGS[tag],
            native_ref=native_ref,
        )
    if tag == "li":
        return BackendBlock(
            block_type="list_item",
            text=_text_of(element),
            level=max(1, list_depth),
            native_ref=native_ref,
        )
    if tag == "img":
        structure = {
            "src": element.get("src", ""),
            "alt": element.get("alt", ""),
        }
        return BackendBlock(
            block_type="figure", text="", structure=structure,
            native_ref=native_ref,
        )
    if tag == "table":
        rows, cols, cells, clamped = _table_grid(element)
        if rows == 0:
            return None
        structure = {"rows": rows, "cols": cols, "cells": cells}
        if clamped:
            # 不可信 span 声明被上限截断（§7.4 可见性，评审 HIGH-2）
            structure["clamped_spans"] = clamped
        return BackendBlock(
            block_type="table",
            text="",
            native_ref=native_ref,
            structure=structure,
        )
    text = _text_of(element)
    if not text:
        return None
    block_type = "title" if tag == "title" else (
        "code" if tag in ("pre", "code") else "paragraph"
    )
    return BackendBlock(
        block_type=block_type, text=text, native_ref=native_ref
    )


def _text_of(element: Any) -> str:
    """inline 子标签文本收拢（itertext），去首尾空白."""
    return "".join(element.itertext()).strip()


_COVERED = object()  # 被合并覆盖的网格位置（占位，不产 cell）


def _nearest_table_within(element: Any, stop_at: Any) -> Any:
    """在 stop_at 子树内向上找最近的 table 祖先（含 stop_at 自身）。

    返回 stop_at => 该行属于外层表（含经 thead/tbody 包裹的常规情形）；
    返回其他 table => 行属于嵌套内层表；None => 无 table 祖先。
    """
    node = element.getparent()
    while node is not None:
        if isinstance(node.tag, str) and node.tag == "table":
            return node
        if node is stop_at:
            return stop_at
        node = node.getparent()
    return None



def _table_grid(
    table: Any
) -> tuple[int, int, list[dict[str, Any]], int]:
    """tr/td 直读 + rowspan/colspan 展开 -> (rows, cols, cells).

    逻辑行号跟随 ``<tr>`` 顺序（rowspan 溢出行只做占位，不挤占后续
    ``<tr>``）；被合并覆盖的位置不产 cell；is_header = th 或首行约定。
    """
    origin_cells: list[dict[str, Any]] = []
    occupied: dict[tuple[int, int], Any] = {}
    clamped = 0  # 被上限截断的 span 声明数（不可信输入防御，评审 HIGH-2）
    tr_count = 0
    # 仅遍历**直接**子行（评审 MED：iter("tr") 是递归后代遍历，嵌套
    # table 的行会被并入外层网格；内层表格由 td 文本收拢，不结构化）。
    for tr in (child for child in table.iterdescendants()
               if isinstance(child.tag, str) and child.tag == "tr"):
        if _nearest_table_within(tr, table) is not table:
            continue  # 属于嵌套内层 table 的行，跳过
        r = tr_count  # 逻辑行号
        tr_count += 1
        c = 0
        for td in tr:
            if not isinstance(td.tag, str) or td.tag not in ("td", "th"):
                continue
            while (r, c) in occupied:
                c += 1
            row_span, row_clamped = _span_attr(td, "rowspan")
            col_span, col_clamped = _span_attr(td, "colspan")
            if row_clamped or col_clamped:
                clamped += 1
            cell = {
                "row_index": r,
                "column_index": c,
                "text": _text_of(td),
                "row_span": row_span,
                "column_span": col_span,
                "is_header": td.tag == "th" or r == 0,
            }
            origin_cells.append(cell)
            for rr in range(r, r + row_span):
                for cc in range(c, c + col_span):
                    occupied[(rr, cc)] = cell if rr == r else _COVERED
            c += col_span
    rows = tr_count
    if occupied:
        rows = max(rows, max(r for r, _ in occupied) + 1)
    cols = max((c + 1 for _, c in occupied), default=0)
    return rows, cols, origin_cells, clamped


#: 声明几何上限（评审 HIGH-2）：不可信的 rowspan/colspan 声明值上限。
#: 超限截断为 1 并交由调用方 warning——防止一条 rowspan="2000000000"
#: 属性把 occupied 字典撑爆（内存 DoS）。10k 已远超真实文档合理值。
_MAX_DECLARED_SPAN = 10_000


def _span_attr(td: Any, name: str) -> tuple[int, bool]:
    """rowspan/colspan 属性直读 -> (值, 是否被截断)。

    非法/缺失回退 1；声明值超 ``_MAX_DECLARED_SPAN`` 截断为 1（按未合并
    处理）并置截断标记，调用方累计 warning——"缺可以，但应可见"（§7.4）。
    """
    raw = td.get(name, "1")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1, False
    if value < 1:
        return 1, False
    if value > _MAX_DECLARED_SPAN:
        return 1, True
    return value, False


# ---------------------------------------------------------------------------
# Normalizer（SRS §C07）
# ---------------------------------------------------------------------------

class HtmlNormalizer(BaseNativeNormalizer):
    """HTML backend artifact -> Parse IR：dom_document 容器 + heading 链."""

    normalizer_version = "native-html@1"
    _default_fingerprints = {NATIVE_HTML_PARSER_ID: NATIVE_HTML_FINGERPRINT}
    _element_type_map = {
        "title": "title",
        "heading": "heading",
        "paragraph": "paragraph",
        "list_item": "list_item",
        "code": "code",
        "table": "table",
        "figure": "figure",
    }

    def _build_containers(self, artifact) -> tuple[Container, ...]:
        # HTML 无页概念：单一 dom_document 容器，page_number 不伪造。
        return (Container(
            container_id=DOM_CONTAINER_ID,
            container_type="dom_document",
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
    "DOM_CONTAINER_ID",
    "NATIVE_HTML_FINGERPRINT",
    "NATIVE_HTML_MIMES",
    "NATIVE_HTML_PARSER_ID",
    "NATIVE_HTML_VERSION",
    "HtmlNormalizer",
    "NativeHtmlParser",
]
