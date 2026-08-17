"""Native PDF parser adapter（M3, SRS §C06 / §4.6, ADR-0003 D-028A）.

核心理念：pdfplumber（pdfminer.six 之上）自带表格网格提取、字符/行级
坐标与字体信息，本模块**只做映射，不写解析算法**：

- ``page.extract_words(use_text_flow=False, keep_blank_chars=False,
  extra_attrs=["size"])`` 的 words 按纵坐标聚类成 textline（top 容差），
  每行产一个 paragraph/heading 块；heading 判定是**映射启发式**：行字号
  （word size 中位数）> 1.15 × 该页字符字号众数且行长较短 -> heading。
  该规则置信度有限，标注在 structure（``heading_rule`` /
  ``type_confidence``），由 Normalizer 落到 ``confidence.type < 0.7``，
  **不冒充高置信**（SRS §7.4）。
- ``page.find_tables()`` + ``table.rows[i].cells[j]`` 坐标网格 +
  ``table.extract()`` -> table 块：相同 bbox 的相邻网格位置即合并单元格，
  由坐标网格推 row_span/col_span（映射逻辑，非解析算法）。表格区域内的
  words 跳过，避免重复正文。
- 页眉页脚判定留 TODO（M4 Reconciler 职责），本适配器不做。
- 加密 PDF：pdfplumber 打开抛 ``PdfminerException``（args[0] 为 pdfminer
  的 ``PDFPasswordIncorrect``）-> 包 :class:`EncryptedDocument`（code
  ``encrypted_document``）；其余第三方异常统一包
  :class:`ParserAdapterError`，不得穿越适配层（SRS §C06）。
- 无文本层（整页无字符）-> 该页产一个 ``warning`` 块记录，不伪造内容。
"""
from __future__ import annotations

from collections import Counter
from io import BytesIO
from statistics import median
from typing import Any

import pdfplumber
from pdfplumber.utils.exceptions import PdfminerException

from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
    ParserAdapterError,
    ParserDescriptor,
    UnsupportedFormat,
)

# --- 身份与指纹（SRS §C04 descriptor / §3.5 parser_fingerprint） -------------


class EncryptedDocument(ParserAdapterError):
    """PDF 加密且无法用空口令打开（SRS §4.6 error 归一化）."""

    code = "encrypted_document"


NATIVE_PDF_PARSER_ID = "native_pdf"
NATIVE_PDF_VERSION = "1.0.0"
NATIVE_PDF_FINGERPRINT = (
    f"{NATIVE_PDF_PARSER_ID}@{NATIVE_PDF_VERSION}"
    f"#pdfplumber-{pdfplumber.__version__}"
)
NATIVE_PDF_MIMES = frozenset({"application/pdf"})

# heading 字号启发式参数（一级近似，映射规则非模型）。
HEADING_SIZE_RATIO = 1.15
HEADING_MAX_LINE_CHARS = 60
HEADING_TYPE_CONFIDENCE = 0.6

# words -> textline 聚类的纵向容差（pt）。
LINE_TOP_TOLERANCE = 3.0


class NativePdfParser:
    """DocumentParser 实现：pdfplumber 内置能力的映射层（SRS §C06）."""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=NATIVE_PDF_PARSER_ID,
            display_name="Native PDF Parser (pdfplumber)",
            version=NATIVE_PDF_VERSION,
            supported_mimes=NATIVE_PDF_MIMES,
            backend_kind="local",
            parser_fingerprint=NATIVE_PDF_FINGERPRINT,
            capabilities=frozenset({"pages", "tables", "coordinates"}),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{NATIVE_PDF_PARSER_ID} cannot parse mime {mime!r}"
            )
        try:
            pdf = pdfplumber.open(BytesIO(data))
        except PdfminerException as exc:
            raise _wrap_open_error(exc) from exc
        except Exception as exc:  # 第三方异常不得穿越适配层（SRS §C06）
            raise ParserAdapterError(f"pdf open failed: {exc}") from exc

        with pdf:
            blocks: list[BackendBlock] = []
            warnings: list[str] = []
            page_sizes: list[tuple[float, float]] = []
            try:
                for index, page in enumerate(pdf.pages):
                    produced, page_warnings = _page_to_blocks(page, index)
                    blocks.extend(produced)
                    warnings.extend(page_warnings)
                    page_sizes.append((float(page.width), float(page.height)))
            except Exception as exc:
                # 中段损坏（如 pdfminer MalformedPDFException 在第 N 页）
                # 也归一，不裸抛第三方异常（§C06，评审 MED）。
                if isinstance(exc, ParserAdapterError):
                    raise
                raise ParserAdapterError(
                    f"{NATIVE_PDF_PARSER_ID}: failed to walk pages: {exc}"
                ) from exc
            usage: dict[str, Any] = {
                "pages": len(pdf.pages),
                "page_sizes": page_sizes,
            }
        return BackendParseArtifact(
            parser_id=NATIVE_PDF_PARSER_ID,
            parser_version=NATIVE_PDF_VERSION,
            mime=mime.lower(),
            blocks=tuple(blocks),
            raw_output="",  # 二进制格式无解码原文，replay 直接重跑 parse
            warnings=tuple(warnings),
            usage=usage,
        )


def _wrap_open_error(exc: PdfminerException) -> ParserAdapterError:
    """把 pdfplumber 打开期异常归一为适配层错误家族（加密单独分类）."""
    inner = exc.args[0] if exc.args else exc.__context__
    name = type(inner).__name__ if inner is not None else ""
    if "Password" in name or "Encryption" in name:
        return EncryptedDocument(
            "PDF is encrypted and no empty-password text layer is available"
        )
    return ParserAdapterError(f"pdf open failed: {exc or name}")


# ---------------------------------------------------------------------------
# 每页映射（模块级纯函数，便于单测）
# ---------------------------------------------------------------------------


def _page_to_blocks(
    page: Any, index: int
) -> tuple[list[BackendBlock], list[str]]:
    """一页 -> (BackendBlock 序列, warnings)。阅读序按 top 升序."""
    container_ref = {"container_type": "page", "index": index}
    if not page.chars:
        return [_no_text_layer_block(container_ref)], [
            f"page {index}: no text layer (no extractable characters)"
        ]

    modal_size = _modal_char_size(page.chars)
    tables = page.find_tables()
    table_blocks = [
        _table_block(table, k, index) for k, table in enumerate(tables)
    ]
    table_boxes = [tuple(table.bbox) for table in tables]

    words = page.extract_words(
        use_text_flow=False,
        keep_blank_chars=False,
        extra_attrs=["size"],
    )
    lines = _group_words_into_lines(
        w for w in words if not _in_any_box(w, table_boxes)
    )
    text_blocks = [
        _line_block(line, modal_size, container_ref) for line in lines
    ]

    ordered = _sort_by_top(text_blocks + table_blocks)
    return ordered, []


def _no_text_layer_block(container_ref: dict[str, Any]) -> BackendBlock:
    """无文本层页的 warning 块：只记录事实，不伪造内容（SRS §7.4）."""
    return BackendBlock(
        block_type="warning",
        text="",
        container_ref=container_ref,
        structure={"reason": "no_text_layer"},
    )


def _modal_char_size(chars: list[dict[str, Any]]) -> float:
    """字符字号众数（正文基准）。返回 0 防御性兜底，不抛异常。"""
    counter = Counter(round(float(c.get("size") or 0.0), 1) for c in chars)
    return counter.most_common(1)[0][0] if counter else 0.0


def _group_words_into_lines(
    words: Any,
) -> list[list[dict[str, Any]]]:
    """words 按 top 容差聚类成行（常见 textline 映射做法）."""
    lines: list[list[dict[str, Any]]] = []
    anchor: float | None = None
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if anchor is None or abs(word["top"] - anchor) > LINE_TOP_TOLERANCE:
            lines.append([])
            anchor = word["top"]
        lines[-1].append(word)
    return lines


def _line_block(
    line: list[dict[str, Any]],
    modal_size: float,
    container_ref: dict[str, Any],
) -> BackendBlock:
    """一行 words -> paragraph/heading 块（字号启发式判 heading）."""
    text = " ".join(w["text"] for w in line)
    bbox = (
        min(w["x0"] for w in line),
        min(w["top"] for w in line),
        max(w["x1"] for w in line),
        max(w["bottom"] for w in line),
    )
    line_size = round(median(float(w.get("size") or 0.0) for w in line), 2)
    is_heading = (
        modal_size > 0
        and line_size > HEADING_SIZE_RATIO * modal_size
        and len(text) <= HEADING_MAX_LINE_CHARS
    )
    if not is_heading:
        return BackendBlock(
            block_type="paragraph",
            text=text,
            container_ref=container_ref,
            bbox=bbox,
        )
    return BackendBlock(
        block_type="heading",
        text=text,
        container_ref=container_ref,
        bbox=bbox,
        level=1,  # 字号启发式只有一级近似，不伪造多级
        structure={
            "heading_rule": "font_size_ratio",
            "line_size": line_size,
            "modal_size": round(modal_size, 2),
            "type_confidence": HEADING_TYPE_CONFIDENCE,
        },
    )


def _table_block(table: Any, table_index: int, page_index: int) -> BackendBlock:
    """pdfplumber Table -> table 块（cells 坐标网格推 span）."""
    structure = _table_structure(table)
    return BackendBlock(
        block_type="table",
        text=_table_text(structure),
        container_ref={"container_type": "page", "index": page_index},
        bbox=tuple(float(v) for v in table.bbox),
        native_ref={"page": page_index, "table_index": table_index},
        structure=structure,
    )


def _table_structure(table: Any) -> dict[str, Any]:
    """rows[i].cells[j] 坐标网格 + extract() 文本 -> 行列/单元格/span.

    pdfplumber 中合并单元格表现为多个网格位置共享同一 bbox，且
    ``extract()`` 在被覆盖位置返回 None；相同 bbox 的网格位置分组合并
    为一个逻辑单元格，span 由该组在网格上的行列延展推出。
    """
    rows = list(table.rows)
    n_rows = len(rows)
    n_cols = max((len(r.cells) for r in rows), default=0)
    text_grid = table.extract() or []

    groups: dict[tuple[float, ...], list[tuple[int, int]]] = {}
    for i, row in enumerate(rows):
        for j, bbox in enumerate(row.cells or []):
            if bbox is None:
                continue
            groups.setdefault(tuple(float(v) for v in bbox), []).append((i, j))

    cells: list[dict[str, Any]] = []
    for positions in groups.values():
        i0 = min(i for i, _ in positions)
        j0 = min(j for _, j in positions)
        row_span = max(i for i, _ in positions) - i0 + 1
        col_span = max(j for _, j in positions) - j0 + 1
        raw = ""
        if i0 < len(text_grid) and j0 < len(text_grid[i0]):
            raw = text_grid[i0][j0] or ""
        cells.append({
            "row_index": i0,
            "column_index": j0,
            "row_span": row_span,
            "column_span": col_span,
            "text": str(raw),
        })
    cells.sort(key=lambda c: (c["row_index"], c["column_index"]))
    return {"rows": n_rows, "cols": n_cols, "cells": cells}


def _table_text(structure: dict[str, Any]) -> str:
    """表格元素文本的一维化（tab 分列、换行分行），仅为可读性."""
    return "\n".join(
        "\t".join(
            c["text"] for c in structure["cells"]
            if c["row_index"] == r and c["text"]
        )
        for r in range(structure["rows"])
    )


def _in_any_box(word: dict[str, Any], boxes: list[tuple[Any, ...]]) -> bool:
    """word 是否落在任一表格 bbox 内（跳过，避免与表格重复）."""
    x_mid = (word["x0"] + word["x1"]) / 2
    y_mid = (word["top"] + word["bottom"]) / 2
    return any(
        box[0] <= x_mid <= box[2] and box[1] <= y_mid <= box[3]
        for box in boxes
    )


def _sort_by_top(blocks: list[BackendBlock]) -> list[BackendBlock]:
    """按 bbox top 升序稳定排序（阅读序一级近似）."""
    return sorted(
        blocks,
        key=lambda b: (b.bbox[1] if b.bbox else 0.0),
    )


__all__ = [
    "EncryptedDocument",
    "NATIVE_PDF_FINGERPRINT",
    "NATIVE_PDF_MIMES",
    "NATIVE_PDF_PARSER_ID",
    "NATIVE_PDF_VERSION",
    "NativePdfParser",
]
