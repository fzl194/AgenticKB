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

# 表格识别策略（真实论文验收三轮修正）：
# - 主策略 MIXED：横线切行 + 文本对齐切列——三线表有横线无竖线，
#   纯 lines 找不到列、纯 text 把整页正文当表；混合两者正好。
# - 回退 TEXT：无横线的表格；须通过紧凑+有效率过滤（假表防御）。
TABLE_SETTINGS_MIXED = {
    "horizontal_strategy": "lines",
    "vertical_strategy": "text",
}
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
                # 档位频次过滤（验收 v2）：档位表只用出现 ≥3 次的字号，
                # 封面/内封装饰大字（1-2 次）不再污染层级。
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
    # 数据核心收缩（验收 v6）：上下框陷阱中候选区域上半部是标题/正文；
    # 按行片段数（列对齐信号）求收缩框。pdfplumber Table.bbox 只读且
    # crop 重查会丢行——收缩结果走旁路表：``_table_block`` 以收缩框
    # 报告 bbox，正文过滤（table_boxes）也用收缩框，使标题/正文行
    # 不再被当作表内内容剔除；表格行列数据仍用原 extract（顶部偶带
    # 一行页眉文本，由家具标注兜底）。
    shrink_overrides: dict[int, tuple] = {}
    for ti, table in enumerate(tables):
        bx = tuple(table.bbox)
        region_chars = [
            c for c in page.chars
            if bx[0] <= c["x0"] <= bx[2] and bx[1] <= c["top"] <= bx[3]
        ]
        region_lines = [
            {**ln, "_chars": [
                c for c in region_chars
                if ln["bbox"][1] <= c["top"] <= ln["bbox"][3]
            ]}
            for ln in group_chars_into_lines(region_chars)
        ]
        target = shrink_bbox_to_data_core(bx, region_lines)
        if target != bx:
            shrink_overrides[ti] = target
    table_blocks = [
        _table_block(
            table, k, index,
            bbox_override=shrink_overrides.get(k),
        )
        for k, table in enumerate(tables)
    ]
    table_boxes = [
        shrink_overrides.get(k) or tuple(table.bbox)
        for k, table in enumerate(tables)
    ]

    # CJK 修复（真实论文验收）：extract_words 按空格分词，中文连排会被
    # 拆成单字碎片——直接用 chars 聚行并按 CJK 规则拼接。随后：
    # (1) 字号启发式先摘出 heading 行（标题是独立块，不参与段落合并，
    #     否则大字号标题会被粘进邻近正文段）；
    # (2) 剩余正文行按 gap 聚成段落（段内行距 vs 段间距信号，验收
    #     第二轮修复"一行一行"问题）。
    lines = group_chars_into_lines(
        [c for c in page.chars if not _in_any_box(c, table_boxes)]
    )
    heading_lines: list[dict[str, Any]] = []
    body_lines: list[dict[str, Any]] = []
    for ln in lines:
        size = float(ln.get("size") or 0.0)
        by_size = (
            modal_size > 0
            and size > HEADING_SIZE_RATIO * modal_size
            and len(ln["text"]) <= HEADING_MAX_LINE_CHARS
        )
        # 编号标题（验收 v3，通用规则非定制）：与正文同字号的
        # "第X章 …"/"1.2.3 …" 是中文学术/技术文档的通行标题形态，
        # 字号启发式天然盲区；两个信号任一命中即为 heading。
        by_pattern = numbered_heading_level(ln["text"]) is not None
        if by_size or by_pattern:
            enriched = dict(ln)
            if by_pattern and not by_size:
                enriched["size"] = size or modal_size  # 保持档位可映射
                enriched["heading_by_pattern"] = True
            heading_lines.append(enriched)
        else:
            body_lines.append(ln)
    heading_lines = merge_heading_runs(
        heading_lines, intra_gap=_paragraph_gap_threshold(body_lines)
    )
    paragraphs = group_lines_into_paragraphs(
        body_lines, intra_gap_threshold=_paragraph_gap_threshold(body_lines)
    )
    # 注：早期"_shrink_table_below_headings"已被数据核心收缩
    # （shrink_bbox_to_data_core，旁路 bbox_override）取代并移除接线。
    text_blocks = [
        _line_block(ln, modal_size, container_ref, doc_heading_sizes)
        for ln in [*heading_lines, *paragraphs]
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


def _paragraph_gap_threshold(lines: list[dict[str, Any]]) -> float:
    """段落断行阈值：页内行间隙中位数 × 1.7（自适应字号/排版）.

    真实论文段内 gap ≈7.4pt、段间 ≈22.8pt——中位数(≈7.4)×1.7≈12.6
    恰好落在两者之间。中位数天然是"段内行距"（正文行占多数）。
    """
    if len(lines) < 3:
        return float("inf")  # 行太少不聚合
    gaps = sorted(
        float(b["bbox"][1]) - float(a["bbox"][3])
        for a, b in zip(lines, lines[1:])
        if float(b["bbox"][1]) > float(a["bbox"][3])
    )
    if not gaps:
        return float("inf")
    mid = gaps[len(gaps) // 2]
    return mid * 1.7 if mid > 0 else float("inf")


def _find_tables_with_fallback(page: Any) -> list[Any]:
    """默认（lines）策略找不到表格时回退 text 策略.

    学术论文三线表普遍无横线，pdfplumber 默认策略识别不到；text 策略
    按文本对齐推断，能覆盖该形态（真实论文验收发现，p28 三线表）。
    text 策略对稀疏正文有误报（单列文本被当表），故回退结果必须
    ≥2 行且 ≥2 列才接受；两次都失败（异常）返回空。
    """
    # 三层策略（验收三轮修正）：默认（全线条线框表，最可靠）→
    # mixed（横线切行+文本切列，三线表）→ text（无横线表，误报高，
    # 仅前两层空时尝试且须通过紧凑+有效率过滤）。
    for settings in (None, TABLE_SETTINGS_MIXED):
        accepted: list[Any] = []
        try:
            found = page.find_tables(settings) if settings else page.find_tables()
            for table in found:
                if _is_plausible_table(table, page):
                    accepted.append(table)
            if accepted:
                return accepted
        except Exception:  # noqa: BLE001
            continue
    accepted = []
    try:
        for table in page.find_tables(TABLE_SETTINGS_TEXT):
            if _is_plausible_table(table, page):
                accepted.append(table)
    except Exception:  # noqa: BLE001 —— 表格识别失败不阻断整页解析
        pass
    if accepted:
        return accepted
    # 第四层（验收 v7）：片段块聚类——数据行（列对齐）自建候选框，
    # crop 内用 text 策略提取。覆盖无线三线表/上下框陷阱后仍能救回
    # 真表（p27 场景：主候选被拒后，text 整页粘连，真表 16 行藏在其中）。
    try:
        return _tables_from_fragment_blocks(page)
    except Exception:  # noqa: BLE001
        return []


def _tables_from_fragment_blocks(page: Any) -> list[Any]:
    """多片段行块 -> crop 内 text 提取的表（第四层候选来源）."""
    lines = group_chars_into_lines(page.chars)
    enriched = []
    for ln in lines:
        cs = [
            c for c in page.chars
            if ln["bbox"][1] <= c["top"] <= ln["bbox"][3]
        ]
        enriched.append({**ln, "segments": _line_segments(cs)})
    blocks = cluster_fragment_lines(enriched)
    out: list[Any] = []
    for block in blocks:
        top = min(l["bbox"][1] for l in block) - 3
        bottom = max(l["bbox"][3] for l in block) + 3
        left = min(l["bbox"][0] for l in block) - 3
        right = max(l["bbox"][2] for l in block) + 3
        cropped = page.crop((left, top, right, bottom))
        found = cropped.find_tables({
            "horizontal_strategy": "text",
            "vertical_strategy": "text",
            "snap_tolerance": 4,
        })
        for table in found:
            if _is_plausible_table(table, page):
                out.append(table)
    return out


def _line_segments(line_chars: list[dict[str, Any]], gap: float = 20.0) -> int:
    """一行字符的文本片段数（x 间隔 >gap 断开）——列对齐信号."""
    if not line_chars:
        return 0
    frags: list[list[float]] = []
    for c in sorted(line_chars, key=lambda c: c["x0"]):
        if frags and c["x0"] - frags[-1][1] < gap:
            frags[-1][1] = max(frags[-1][1], c["x1"])
        else:
            frags.append([c["x0"], c["x1"]])
    return len(frags)


def cluster_fragment_lines(
    lines: list[dict[str, Any]], max_gap: float = 45, min_rows: int = 2
) -> list[list[dict[str, Any]]]:
    """多片段行聚成连续块（第四层表格候选，验收 v7）.

    数据行（segments >=2，列对齐）按 top 排序后，行间隙 <= ``max_gap``
    聚为同块；块内行数 >= ``min_rows`` 才输出（孤行多为图注/页脚）。
    与数据核心收缩共用同一信号（行片段数），非文档特定。
    """
    multi = sorted(
        (ln for ln in lines if int(ln.get("segments") or 1) >= 2),
        key=lambda ln: ln["bbox"][1],
    )
    blocks: list[list[dict[str, Any]]] = []
    for ln in multi:
        if blocks and ln["bbox"][1] - blocks[-1][-1]["bbox"][3] <= max_gap:
            blocks[-1].append(ln)
        else:
            blocks.append([ln])
    return [b for b in blocks if len(b) >= min_rows]


def shrink_bbox_to_data_core(
    bbox: tuple, region_lines: list[dict[str, Any]]
) -> tuple:
    """表格 bbox 收缩到"数据核心区"（验收 v6，通用规则）.

    上下框陷阱中，候选区域上半部是标题/正文（每行 1 个文本片段），
    下半部才是真表格数据（每行 >=2 片段——列对齐）。规则：
    - 找出全部多片段行（``segments >= 2``）；
    - 若这些行的 top 最大间隔 > 1.8×区域行距中位数（说明中间夹了
      非表格内容），只取**连续行块**（从最下方块起）；
    - bbox 顶边收缩到该块首行 top；无多片段行则原样返回（另行由
      调用方的列校验拒绝）。
    """
    multi = [
        ln for ln in region_lines
        if int(ln.get("segments") or 1) >= 2 or _line_segments(
            ln.get("_chars") or []
        ) >= 2
    ]
    if not multi:
        return tuple(bbox)
    # 连续块：按 top 排序，行距异常处断开，取含最多行的末块
    multi.sort(key=lambda ln: ln["bbox"][1])
    blocks: list[list[dict[str, Any]]] = [[multi[0]]]
    for prev, cur in zip(multi, multi[1:]):
        gap = cur["bbox"][1] - prev["bbox"][3]
        blocks[-1].append(cur) if gap <= 30 else blocks.append([cur])
    core = max(blocks, key=len) if len(blocks) > 1 else blocks[0]
    top = min(ln["bbox"][1] for ln in core)
    # 守卫（区分两类场景）：真表格区域里多片段行占**多数**（表头+数据
    # 行都列对齐）；上下框陷阱里多片段行只是末尾小部（上部全是单片段
    # 标题/正文）。多片段行占比 <40% 时收缩可疑（可能误伤），放弃。
    total_lines = len(region_lines)
    multi_ratio = len(multi) / total_lines if total_lines else 0.0
    if multi_ratio < 0.40 and (top - bbox[1]) > (bbox[3] - bbox[1]) * 0.5:
        return tuple(bbox)
    return (bbox[0], max(bbox[1], top), bbox[2], bbox[3])


def _region_has_column_structure(
    region_lines: list[dict[str, Any]],
) -> bool:
    """区域文本是否呈列对齐结构（表格语义校验，验收 v5）.

    上下框陷阱：仅两条横线夹住的正文（章标题+正文段）会被 mixed 策略
    圈成"表"。真表格的单元格文本在列方向对齐——把区域按行聚出的文本
    片段 x0 做簇分析，**同一纵向位置出现 >=2 行**才算一列；有效列数
    >=2 才是表格。
    """
    if len(region_lines) < 2:
        return False
    # (x0, x1) 文本片段；行间允许 gap
    x_starts = sorted(
        (ln["x0"], ln["x1"]) for ln in region_lines if ln.get("text")
    )
    if not x_starts:
        return False
    # 贪心聚列簇：x0 相差 <12pt 归同簇
    columns: list[list[tuple[float, float]]] = [[x_starts[0]]]
    for xs in x_starts[1:]:
        if xs[0] - columns[-1][-1][0] < 12:
            columns[-1].append(xs)
        else:
            columns.append([xs])
    effective_cols = sum(1 for col in columns if len(col) >= 2)
    return effective_cols >= 2


def _is_plausible_table(table: Any, page: Any) -> bool:
    """表格合理性判定（真实论文验收三轮修正）.

    - 结构：≥2 行且 ≥2 列；
    - 紧凑：表格面积 ≤60% 页面（整页正文流假表通常 >60%）；
    - 有效率：非空格占比 ≥40%（纯 text 假表空格子占大半）；
    - 行数上限：≤35 行（整页文本流假表 50-77 行）。
    """
    try:
        rows = len(table.rows)
        cols = max((len(r.cells) for r in table.rows), default=0)
        if rows < 2 or cols < 2 or rows > 35:
            return False
        bx = table.bbox
        area = (bx[2] - bx[0]) * (bx[3] - bx[1]) / (page.width * page.height)
        if area > 0.60:
            return False
        ratio = _table_grid_effective_ratio(table.extract() or [])
        if ratio < 0.40:
            return False
        # 上下框陷阱防御（验收 v5/v6）：三个网格级校验。
        # 1) 有效列数 >=2（"仅两条横线夹住的正文"多数行 1 列非空）；
        # 2) 无跨列文本行（竖切列若把同一文字行切碎——行内多个相邻格
        #    有内容且拼接后是连续正文——说明列边界切在文字中间，
        #    p27 场景：标题/正文被切成 4 列"内容"骗过列数校验）。
        #    判定：格子文本以中缀（无标点/空格结尾）断裂跨格 >=2 次即拒。
        grid = table.extract() or []
        n_cols = max((len(row) for row in grid), default=0)
        if n_cols < 2:
            return False
        col_filled = [0] * n_cols
        import re as _re

        def _row_breaks(cells: list[str]) -> int:
            breaks = 0
            for a, b in zip(cells, cells[1:]):
                if a and b and a[-1] not in "。；，、：）)] " and b[0] not in "（([ ":
                    breaks += 1
            return breaks

        def _is_caption_row(cells: list[str]) -> bool:
            """题注行：'表 N-N'/'Table N-N' 开头或跨全列的单行内容."""
            joined = "".join(cells)
            return bool(
                _re.match(r"^(表\s*\d|Table\s*\d)", joined)
                or sum(1 for c in cells if c) <= 1
            )

        split_word_rows = 0
        data_rows = 0
        for row in grid:
            cells = [str(c).strip() if c else "" for c in row]
            for ci, cell in enumerate(cells):
                if cell and ci < n_cols:
                    col_filled[ci] += 1
            if _is_caption_row(cells):
                continue  # 题注跨列居中是正常形态
            data_rows += 1
            if _row_breaks(cells) >= 2:
                split_word_rows += 1
        effective_cols = sum(1 for n in col_filled if n >= 2)
        if effective_cols < 2:
            return False
        # 多数**数据行**的文本被列边界切碎 = 列切在文字中间 = 不是表
        if data_rows and split_word_rows > data_rows / 2:
            return False
        return True
    except Exception:  # noqa: BLE001 —— 单表判定失败按不成立处理
        return False
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
    """CJK 表意文字/全角符号判定（拼接规则用）.

    输入可能是**多字符**（pdfplumber 对连字/聚类字符产出如 "fi" 的
    text）——按"全部字符都是 CJK 才算 CJK"处理；空串按非 CJK。
    """
    if not ch:
        return False
    return all(
        0x4E00 <= ord(c) <= 0x9FFF      # CJK Unified Ideographs
        or 0x3000 <= ord(c) <= 0x303F   # CJK 标点
        or 0xFF00 <= ord(c) <= 0xFFEF   # 全角形式
        or 0x3400 <= ord(c) <= 0x4DBF   # 扩展 A
        for c in ch
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


def heading_levels_for(
    sizes: list[float], min_occurrences: int = 1
) -> list[int]:
    """标题行字号 -> 文档级档位 level（两遍式第二遍，验收修复 v2）.

    - 全文档收集 heading 行字号，排序去重映射 1..n（大字号=浅 level）；
    - **频次过滤**（``min_occurrences``，默认 1 保持原行为）：出现次数
      少于阈值的字号（封面/内封装饰大字只出现 1-2 次）**不进档位表**，
      其 heading 映射到不超过自身的最近高频档（26pt 封面字 → 16pt 章
      档）。真实结构标题必然重复出现（每章/每节同字号），这是封面
      装饰字与结构标题的可靠区分信号（验收修复：档位被封面字污染后
      "第一章"被垫到第 5 层）。
    """
    if not sizes:
        return []
    from collections import Counter

    counts = Counter(round(float(s), 1) for s in sizes)
    frequent = sorted(
        (size for size, n in counts.items() if n >= min_occurrences),
        reverse=True,
    )
    if not frequent:
        return [1] * len(sizes)
    rank = {size: i + 1 for i, size in enumerate(frequent)}

    def _level_of(size: float) -> int:
        key = round(float(size), 1)
        if key in rank:
            return rank[key]
        # 稀有字号：向"不超过自身的最近高频档"取整
        below = [s for s in frequent if s < key]
        return rank[max(below)] if below else 1

    return [_level_of(s) for s in sizes]


#: 中文章节词（第一章…第九章，支持"第十二章"）。
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def merge_heading_runs(
    heading_lines: list[dict[str, Any]], intra_gap: float
) -> list[dict[str, Any]]:
    """相邻 heading 行合并（通用规则，验收 v4）.

    长标题在 PDF 中常被排版折成多行（"第三章 …性 / 能研究"），字号与
    行距都与单行标题一致。规则：相邻 heading 行 gap ≤ 阈值即视为同一
    标题的续行，文本以空格拼接、bbox 联合。与段落聚合同一判据（行距
    信号），非针对特定文档。
    """
    if not heading_lines:
        return []
    merged: list[list[dict[str, Any]]] = [[heading_lines[0]]]
    for prev, cur in zip(heading_lines, heading_lines[1:]):
        gap = float(cur["bbox"][1]) - float(prev["bbox"][3])
        if gap <= intra_gap:
            merged[-1].append(cur)
        else:
            merged.append([cur])
    out: list[dict[str, Any]] = []
    for members in merged:
        text = " ".join(m["text"] for m in members)
        out.append({
            "text": text,
            "bbox": (
                min(m["bbox"][0] for m in members),
                min(m["bbox"][1] for m in members),
                max(m["bbox"][2] for m in members),
                max(m["bbox"][3] for m in members),
            ),
            "size": members[0].get("size"),
            "heading_by_pattern": any(
                m.get("heading_by_pattern") for m in members
            ),
        })
    return out


def _shrink_table_below_headings(table: Any, heading_lines: list[dict[str, Any]]) -> Any:
    """表格 bbox 顶边收缩：heading 行不属表格（通用防御，验收 v4）.

    pdfplumber 的 mixed 策略有时把标题/页眉行圈进表格首行（"第二章…"
    被吞）。规则：若 heading 行与表格 bbox 相交，表格顶边下压到这些行
    的下沿——标题永远不属于表格，这是文档语义而非文档特定。
    """
    bx = list(table.bbox)
    for line in heading_lines:
        lb = line["bbox"]
        # 相交判定（heading 行与表格顶带重叠）
        if bx[0] < lb[2] and lb[0] < bx[2] and lb[1] < bx[3] and lb[3] > bx[1]:
            if lb[3] > bx[1]:
                bx[1] = max(bx[1], lb[3])
    table.bbox = tuple(bx)
    return table


def numbered_heading_level(text: str) -> int | None:
    """中文文档编号标题模式 -> 层级（纯映射规则，验收 v3）.

    - ``第X章 …``（X 为中文数字，支持组合如"十二"）-> level 1；
    - ``1.2 …`` / ``1.2.3 …`` / ``2.3.1.4 …``（1-4 级点分编号 + 空格 +
      标题文字，且标题文字不以标点结尾）-> 编号段数 + 1；
    - 其余 -> None（普通句子/年份/表号"表 2-2"等不匹配）。
    """
    import re

    s = text.strip()
    m = re.match(r"^第([一二三四五六七八九十]+)章\s+\S", s)
    if m:
        digits = m.group(1)
        if len(digits) == 1:
            return 1 if digits in _CN_NUM else None
        # "十二" 组合（10-19），工程上够用
        if len(digits) == 2 and digits[0] == "十" and digits[1] in _CN_NUM:
            return 1
        return None
    m2 = re.match(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s+(\S[^。；，！？]{1,40})$", s)
    if m2:
        # 排除"表 2-2""图 1-1"类引用（开头不是数字已被正则排除）；
        # 排除句尾是标点的普通句（正则已限）；年份如 "2023 年" 不带点分段
        # "1.2"(1个点)->2、"1.2.2"(2个点)->3：层级=点数+1
        return m2.group(1).count(".") + 1
    return None


def group_lines_into_paragraphs(
    lines: list[dict[str, Any]],
    intra_gap_threshold: float,
) -> list[dict[str, Any]]:
    """行 -> 段落聚合（gap 判段，真实论文验收修复）.

    真实中文论文的行距信号非常清晰：段内行距 ≈7.4pt、段间距 ≈22.8pt
    （3 倍差）。本函数按"行间隙 > 阈值即断段"聚合；段落 bbox 覆盖全部
    成员行；文本拼接：上一行以 CJK 结尾且下一行以 CJK 开头 -> 无缝，
    否则加一个空格（Latin 换行处还原词边界）。
    """
    if not lines:
        return []
    paragraphs: list[list[dict[str, Any]]] = [[lines[0]]]
    for prev, cur in zip(lines, lines[1:]):
        gap = float(cur["bbox"][1]) - float(prev["bbox"][3])
        if gap > intra_gap_threshold:
            paragraphs.append([cur])
        else:
            paragraphs[-1].append(cur)

    out: list[dict[str, Any]] = []
    for members in paragraphs:
        texts = [m["text"] for m in members]
        joined = ""
        for i, t in enumerate(texts):
            if i == 0:
                joined = t
            elif (
                _is_cjk(texts[i - 1][-1]) if texts[i - 1] else False
            ) and t and _is_cjk(t[0]):
                joined += t  # CJK 跨行无缝
            else:
                joined += " " + t
        out.append({
            "text": joined,
            "bbox": (
                min(m["bbox"][0] for m in members),
                min(m["bbox"][1] for m in members),
                max(m["bbox"][2] for m in members),
                max(m["bbox"][3] for m in members),
            ),
            "size": members[0].get("size"),
        })
    return out


def _table_grid_effective_ratio(grid: list[list[str | None]]) -> float:
    """text 回退策略的表格有效率（假表过滤，验收修复）.

    page5 英文摘要被 text 策略误判为 78×6 表——单词间距被当成列对齐，
    大半格子是 None。真实表格空格子只占少数；本函数返回非空格占比。
    """
    if not grid:
        return 0.0
    total = sum(len(row) for row in grid)
    filled = sum(
        1 for row in grid for cell in row if cell and str(cell).strip()
    )
    return filled / total if total else 0.0


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
    # 分流已在 _page_to_blocks 完成（字号启发式 ∨ 编号模式）；此处尊重
    # 上游判定——heading_by_pattern 标记的行字号同正文，不能再按字号
    # 重判降级（接缝 bug：模式识别命中后在这里被冲掉，验收 v5）。
    is_heading = bool(line.get("heading_by_pattern")) or (
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
    # level 优先级：编号模式（精确）> 字号档位（启发式，频次过滤后）。
    pattern_level = numbered_heading_level(text)
    if pattern_level is not None:
        level = pattern_level
    elif doc_heading_sizes:
        level = heading_levels_for(
            doc_heading_sizes + [line_size], min_occurrences=3
        )[-1]
    else:
        level = 1
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


def _table_block(
    table: Any,
    table_index: int,
    page_index: int,
    bbox_override: tuple | None = None,
) -> BackendBlock:
    """pdfplumber Table -> table 块（cells 坐标网格推 span）.

    ``bbox_override``：数据核心收缩框（pdfplumber Table.bbox 只读，
    收缩结果经旁路传入；行列数据保持原 extract）。
    """
    structure = _table_structure(table)
    box = bbox_override or tuple(float(v) for v in table.bbox)
    return BackendBlock(
        block_type="table",
        text=_table_text(structure),
        container_ref={"container_type": "page", "index": page_index},
        bbox=tuple(float(v) for v in box),
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
