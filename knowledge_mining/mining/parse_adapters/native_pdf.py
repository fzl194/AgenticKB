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

from dataclasses import replace

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

# 行聚类纵向容差（pt）。取 6pt：覆盖正文行距的同时吸收上下标
# （CO2 的下标 "2" top 偏移约 4-5pt，3pt 容差会把它拆成独立"行"，
# 真实论文验收发现 274 处碎片）。
LINE_TOP_TOLERANCE = 6.0

# 页眉页脚判定阈值（映射层家具标注，真实论文验收修复）：
# - 跨页完全重复 ≥3 页的长行 -> page_header/page_footer；
# - 纯数字/罗马数字短行 -> page_number。
_FURNITURE_REPEAT_PAGES = 3

# 学术三线表回退策略：无横线表格按文本对齐推断（验收修复）。
TABLE_SETTINGS_TEXT = {
    "horizontal_strategy": "text",
    "vertical_strategy": "text",
}


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
                # 两遍式（真实论文验收修复）：第一遍轻量收集全文档的
                # heading 行字号（档位映射输入）；第二遍产块。
                doc_heading_sizes: list[float] = []
                for page in pdf.pages:
                    if not page.chars:
                        continue
                    modal = _modal_char_size(page.chars)
                    if modal <= 0:
                        continue
                    for ln in group_chars_into_lines(page.chars):
                        size = float(ln.get("size") or 0.0)
                        if (
                            size > HEADING_SIZE_RATIO * modal
                            and len(ln["text"]) <= HEADING_MAX_LINE_CHARS
                        ):
                            doc_heading_sizes.append(size)
                for index, page in enumerate(pdf.pages):
                    produced, page_warnings = _page_to_blocks(
                        page, index, doc_heading_sizes=doc_heading_sizes
                    )
                    blocks.extend(produced)
                    warnings.extend(page_warnings)
                    page_sizes.append((float(page.width), float(page.height)))
                _annotate_furniture(blocks)
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
    page: Any,
    index: int,
    doc_heading_sizes: list[float] | None = None,
) -> tuple[list[BackendBlock], list[str]]:
    """一页 -> (BackendBlock 序列, warnings)。阅读序按 top 升序.

    ``doc_heading_sizes``：**文档级**已收集的 heading 行字号（两遍式第二
    遍传入；真实中文论文验收发现单页众数档位在多字号页面上不可靠，
    level 映射需要全文档的标题字号集合，见 :func:`heading_levels_for`）。
    """
    container_ref = {"container_type": "page", "index": index}
    if not page.chars:
        return [_no_text_layer_block(container_ref)], [
            f"page {index}: no text layer (no extractable characters)"
        ]

    modal_size = _modal_char_size(page.chars)
    tables = _find_tables_with_fallback(page)
    table_blocks = [
        _table_block(table, k, index) for k, table in enumerate(tables)
    ]
    table_boxes = [tuple(table.bbox) for table in tables]

    # CJK 修复（真实论文验收）：extract_words 按空格分词，中文连排会被
    # 拆成单字碎片——直接用 chars 聚行并按 CJK 规则拼接。
    lines = group_chars_into_lines(
        [c for c in page.chars if not _in_any_box(c, table_boxes)]
    )
    text_blocks = [
        _line_block(line, modal_size, container_ref, doc_heading_sizes)
        for line in lines
    ]

    ordered = _sort_by_top(text_blocks + table_blocks)
    return ordered, []


def _annotate_furniture(blocks: list[BackendBlock]) -> None:
    """文档级家具标注（真实论文验收修复）：页眉/页码改块类型.

    跨页重复文本按 :func:`classify_furniture` 判定；被标注的块改
    ``page_header``/``page_number`` 类型（Normalizer 会映射为对应
    element_type，下游可按类型过滤而不丢内容）。只改类型与注记，
    不删除任何块（去重是 M4 Reconciler 职责）。
    """
    from collections import defaultdict

    seen: dict[str, set[int]] = defaultdict(set)
    for block in blocks:
        text = (block.text or "").strip()
        if text and block.container_ref:
            index = block.container_ref.get("index")
            if isinstance(index, int):
                seen[text].add(index)

    entries = [
        {"text": text, "pages": pages} for text, pages in seen.items()
    ]
    verdicts = classify_furniture(entries, page_count=len(seen))
    by_text = {
        entry["text"]: verdict
        for entry, verdict in zip(entries, verdicts)
    }
    for i, block in enumerate(blocks):
        furniture = by_text.get((block.text or "").strip())
        if furniture is not None and block.block_type in ("paragraph", "heading"):
            structure = dict(block.structure)
            structure["furniture"] = True
            blocks[i] = replace(
                block, block_type=furniture, structure=structure
            )


def _find_tables_with_fallback(page: Any) -> list[Any]:
    """默认（lines）策略找不到表格时回退 text 策略.

    学术论文三线表普遍无横线，pdfplumber 默认策略识别不到；text 策略
    按文本对齐推断，能覆盖该形态（真实论文验收发现，p28 三线表）。
    text 策略对稀疏正文有误报（单列文本被当表），故回退结果必须
    ≥2 行且 ≥2 列才接受；两次都失败（异常）返回空。
    """
    try:
        found = page.find_tables()
        if found:
            return list(found)
        candidates = page.find_tables(TABLE_SETTINGS_TEXT)
        accepted = []
        for table in candidates:
            try:
                cols = max((len(r.cells) for r in table.rows), default=0)
                if cols >= 2 and len(table.rows) >= 2:
                    accepted.append(table)
            except Exception:  # noqa: BLE001 —— 单表判定失败不影响其余
                continue
        return accepted
    except Exception:  # noqa: BLE001 —— 表格识别失败不阻断整页解析
        return []


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


#: CJK 字符区间（判断拼接时是否加空格 + 双宽步进检测用）。
def _is_cjk(ch: str) -> bool:
    """CJK 表意文字/全角符号判定（拼接规则用）。"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # CJK Unified Ideographs
        or 0x3000 <= code <= 0x303F   # CJK 标点
        or 0xFF00 <= code <= 0xFFEF   # 全角形式
        or 0x3400 <= code <= 0x4DBF   # 扩展 A
    )


def group_chars_into_lines(
    chars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """字符直接聚行 + CJK-aware 文本拼接（真实论文验收修复）.

    pdfplumber 的 ``extract_words`` 以空格分词，中文连排（无空格）会被
    拆成单字 word；本函数绕过 words 直接聚合 ``page.chars``：

    - 按 ``(top, x0)`` 排序、top 容差聚类成行（同 :data:`LINE_TOP_TOLERANCE`）；
    - 行内文本拼接规则：CJK-CJK 之间不加空格；Latin-Latin 之间加空格
      （还原英文词间距）；CJK↔Latin 边界加一个空格（"镍基 MOF"）。
    - 行 ``size`` 取字符字号中位数；``bbox`` 覆盖全行。
    """
    # 纯空白字符不参与行文本（extract_words 原本就会丢弃；保留会带来
    # 行首/行中杂散空格），间距由 x0/x1 计算不需要它们。
    visible = [c for c in chars if (c.get("text") or "").strip()]
    lines: list[list[dict[str, Any]]] = []
    anchor: float | None = None
    for ch in sorted(visible, key=lambda c: (c["top"], c["x0"])):
        if anchor is None or abs(ch["top"] - anchor) > LINE_TOP_TOLERANCE:
            lines.append([])
            anchor = ch["top"]
        lines[-1].append(ch)

    out: list[dict[str, Any]] = []
    for line_chars in lines:
        text = _join_line_text(line_chars)
        if not text.strip():
            continue
        sizes = [float(c.get("size") or 0.0) for c in line_chars]
        out.append({
            "text": text,
            "bbox": (
                min(c["x0"] for c in line_chars),
                min(c["top"] for c in line_chars),
                max(c["x1"] for c in line_chars),
                max(c["bottom"] for c in line_chars),
            ),
            "size": round(median(sizes), 2),
        })
    return out


def _join_line_text(line_chars: list[dict[str, Any]]) -> str:
    """行内字符 -> 文本（CJK/间距感知拼接）。

    - CJK-CJK：无空格；
    - Latin-Latin：按字符间距判断——词内字母紧邻（gap≈0），空格宽度
      约 0.278×字号（Helvetica 度量），阈值取 0.15×字号居中区分；
    - CJK↔Latin 边界：一个空格（"镍基 MOF"）。
    """
    ordered = sorted(
        (c for c in line_chars if c.get("text")), key=lambda c: c["x0"]
    )
    parts: list[str] = []
    prev: dict[str, Any] | None = None
    for c in ordered:
        t = c["text"]
        if prev is not None:
            prev_cjk = _is_cjk(prev["text"])
            cur_cjk = _is_cjk(t)
            gap = float(c["x0"]) - float(prev["x1"])
            size = max(float(c.get("size") or 0.0), 1.0)
            if prev_cjk and cur_cjk:
                pass  # CJK 连排
            elif prev_cjk != cur_cjk:
                parts.append(" ")  # CJK↔Latin 边界
            elif gap > 0.15 * size:
                parts.append(" ")  # Latin 词边界
        parts.append(t)
        prev = c
    return "".join(parts)


_ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
          "xi", "xii", "xiii", "xiv", "xv"}


def classify_furniture(
    lines: list[dict[str, Any]], page_count: int
) -> list[str | None]:
    """行级家具标注（页眉/页脚/页码），纯函数。

    输入行 dict 带 ``text`` 与 ``pages``（该文本出现的页集合）：
    - 跨 ≥3 页完全相同且长度 >12 的行 -> ``page_header``（页码 ≤6 字符
      单独分类，>12 字符的跨页重复不可能是正文；不区分上下，
      位置由调用方 bbox 补充，本函数只按重复性判定）；
    - 纯数字 / 罗马数字的短行（≤6 字符）-> ``page_number``；
    - 其余 -> None（正常内容）。

    只标注不删除（SRS §4.8：页眉页脚去重是 Reconciler 职责；适配层
    先把可判定的家具标出来，M4 可直接消费）。
    """
    verdicts: list[str | None] = []
    for line in lines:
        text = (line.get("text") or "").strip()
        pages = line.get("pages") or set()
        if len(text) > 12 and len(pages) >= _FURNITURE_REPEAT_PAGES:
            verdicts.append("page_header")
            continue
        compact = text.replace(" ", "")
        if len(compact) <= 6 and (
            compact.isdigit() or compact.lower() in _ROMAN
        ):
            verdicts.append("page_number")
            continue
        verdicts.append(None)
    return verdicts


def heading_levels_for(sizes: list[float]) -> list[int]:
    """标题行字号 -> 文档级档位 level（两遍式第二遍，验收修复）.

    单页众数在"正文 12pt/表注 8pt/章题 16pt"页面上会把 8pt 也算档；
    改为**全文档**收集 heading 行字号，排序去重后映射 1..n（大字号 =
    浅 level）。同字号同档，保证章（16pt）=2、节（14pt）=3 这类真实
    层级可恢复。字号集合为空时全部返回 1。
    """
    if not sizes:
        return []
    distinct = sorted(set(round(float(s), 1) for s in sizes), reverse=True)
    rank = {size: i + 1 for i, size in enumerate(distinct)}
    return [rank[round(float(s), 1)] for s in sizes]


def _line_block(
    line: dict[str, Any],
    modal_size: float,
    container_ref: dict[str, Any],
    doc_heading_sizes: list[float] | None = None,
) -> BackendBlock:
    """一行（已聚好的行 dict）-> paragraph/heading 块.

    heading 判定仍是字号启发式（置信度如实降权）；``level`` 由文档级
    档位表 ``doc_heading_sizes`` 映射（无表时回退 1，不伪造多级）。
    """
    text = line["text"]
    bbox = line["bbox"]
    line_size = float(line.get("size") or 0.0)
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
    level = 1
    if doc_heading_sizes:
        level = heading_levels_for(doc_heading_sizes + [line_size])[-1]
    return BackendBlock(
        block_type="heading",
        text=text,
        container_ref=container_ref,
        bbox=bbox,
        level=level,
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
