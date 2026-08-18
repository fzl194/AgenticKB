"""Native PDF parser adapter（M3, SRS §C06 / §4.6, ADR-0003 D-028A；
2026-08-17 整改轮：结构性缺陷修复 + 文档级规则迁出）.

核心理念：pdfplumber（pdfminer.six 之上）自带表格网格提取、字符/行级
坐标与字体信息，本模块**只做映射，不写解析算法**：

- ``page.chars`` 直接聚行（CJK-aware 拼接，D-030），行/段/表产块；
  heading 判定是字号档位 + 编号模式（D-031 验收链），置信度如实降权
  （SRS §7.4）。
- ``find_tables`` 三层策略 + 片段聚类第四层（D-031）；
  **数据核心收缩**后 bbox 与 cell 内容一致（整改轮 P-4）。
- 双栏阅读序：跨沟行才当通栏锚，其余按中心归栏（整改轮 P-1）。
- 标题防御：数字前缀噪声只拦无 CJK 短行（P-2）；片段计数只用行内
  字符（P-3）。
- 家具标注（跨页重复/页码）**已迁至 Structural Reconciler**——adapter
  只做页内规则（用户指令：跨元素/跨页规则不进 adapter）。
- 加密 PDF：pdfplumber 打开抛 ``PdfminerException`` -> 包
  :class:`EncryptedDocument`；其余第三方异常统一包
  :class:`ParserAdapterError`（SRS §C06）。
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
NATIVE_PDF_VERSION = "2.0.0"
NATIVE_PDF_FINGERPRINT = (
    f"{NATIVE_PDF_PARSER_ID}@{NATIVE_PDF_VERSION}"
    f"#pdfplumber-{pdfplumber.__version__}"
)
NATIVE_PDF_MIMES = frozenset({"application/pdf"})

# heading 字号启发式参数（一级近似，映射规则非模型）。
HEADING_SIZE_RATIO = 1.15
HEADING_MAX_LINE_CHARS = 60
HEADING_TYPE_CONFIDENCE = 0.6

# 图注前缀（题注不是标题，通用规范形态）。
import re as _re_mod
_CAPTION_PREFIX = _re_mod.compile(
    r"^\s*(Fig(ure)?\.?\s*\d|图\s*\d[-−—–.]|Table\s*\d|表\s*\d[-−—–.])"
)

# 行聚类纵向容差（pt）。取 6pt：覆盖正文行距的同时吸收上下标
# （CO2 的下标 "2" top 偏移约 4-5pt，3pt 容差会把它拆成独立"行"，
# 真实论文验收发现 274 处碎片）。
LINE_TOP_TOLERANCE = 6.0

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
                # 家具标注（跨页重复/页码）已迁至 Structural Reconciler
                # （IR 级文档规则，整改轮用户指令：adapter 不再膨胀）。
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
            raw_output="",  # 二进制格式无解码原文；replay 走 artifact 序列化（v1.2）
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
    # 不再被当作表内内容剔除；整改轮 P-4 起，cell 内容也按收缩框
    # 过滤并紧凑重排（bbox 与 cell 覆盖一致）。
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
    # 双栏阅读序重排（验收 v8 + 整改轮 P-1）：跨沟通栏行作锚，
    # 栏内行按中心归栏、先左后右。
    lines = reorder_columns(lines, page_width=float(page.width))
    heading_lines: list[dict[str, Any]] = []
    body_lines: list[dict[str, Any]] = []
    for ln in lines:
        size = float(ln.get("size") or 0.0)
        # 页面字号纯度门（验收 v9）：字号众数占页内字符 <50% 时
        # （Proof 稿/图密集页），字号启发式不可靠——禁用，仅编号模式。
        modal_ratio = _modal_size_ratio(page.chars)
        by_size = (
            modal_size > 0
            and modal_ratio >= 0.50
            and size > HEADING_SIZE_RATIO * modal_size
            and len(ln["text"]) <= HEADING_MAX_LINE_CHARS
        )
        # 编号标题（验收 v3，通用规则非定制）：与正文同字号的
        # "第X章 …"/"1.2.3 …" 是中文学术/技术文档的通行标题形态，
        # 字号启发式天然盲区；两个信号任一命中即为 heading。
        # 微缩字防御（验收 v9 收尾）：图内坐标轴标签（1.75-2.5pt，proof
        # 缩放）的 "0.4 nile" 形似点分编号——但真标题字号不可能 <60%
        # 正文。字号下限一票否决两个信号。
        size_floor_ok = size >= 0.60 * modal_size if modal_size > 0 else True
        by_pattern_precheck = (
            numbered_heading_level(ln["text"]) is not None and size_floor_ok
        )
        by_pattern = by_pattern_precheck
        # 图区/图注防御（验收 v9，通用）：图内刻度文字（"0.6 de …"轴刻度）
        # 字号常大于正文被误判标题；图注行（"Fig. N" / "图 N-" / "Table N"
        # 开头）是题注不是标题。
        is_caption = bool(_CAPTION_PREFIX.match(ln["text"]))
        # 整改轮 P-3：片段数只统计**行内**字符（x 落在行 bbox 内）——
        # 此前用全页同 top 带字符，双栏页左栏标题会因右栏正文被判
        # dense_frags 误杀。
        dense_frags = _line_segments(
            [
                c for c in page.chars
                if ln["bbox"][1] <= c["top"] <= ln["bbox"][3]
                and ln["bbox"][0] - 1 <= c["x0"]
                and c["x1"] <= ln["bbox"][2] + 1
            ]
        ) >= 4
        # 文本质量门（验收 v9）：标题须有"词性内容"——字母词 >=2 个
        # （含 CJK 计 1 词）。纯数字/符号串（公式、坐标刻度、图例数值）
        # 不是标题。
        word_count = len(_re_mod.findall(r"[A-Za-z]{2,}", ln["text"]))
        cjk = _re_mod.search(r"[一-鿿]", ln["text"]) is not None
        # 竖排标签粘连检测：x 拆分残余把多个竖排字母粘成"伪词"（如
        # "namshyvritll"），特征是长词中元音占比异常低（<20%）。
        gibberish = any(
            len(w) >= 6 and sum(c in "aeiouAEIOU" for c in w) / len(w) < 0.2
            for w in _re_mod.findall(r"[A-Za-z]{4,}", ln["text"])
        )
        # 竖排轴标签残余（验收 v9 收尾 + 整改轮 P-2 收紧）："0.4 nile" /
        # "0.1 itci"——数字前缀 + 单个短乱序词的**短行**。真标题形态
        # （"3D Printing Technology"/含 CJK）有多个词或 CJK 内容，不受
        # 此防御影响（整改轮 P-2 误杀修复）。
        digit_leading_noise = (
            bool(_re_mod.match(r"^[\d.\s-]+[A-Za-z]", ln["text"].strip()))
            and not by_pattern_precheck
            and not cjk
            and word_count <= 1
            and len(ln["text"].strip()) <= 12
        )
        text_is_wordy = (
            word_count >= 2 or (word_count >= 1 and cjk)
        ) and not gibberish and not digit_leading_noise
        # 句子形态防御（验收 v9 + 整改轮 P-2）：小写开头不是标题。
        # 数字开头不再并入小写判定（"3D 打印…"/"5G 网络…"是真标题，
        # 噪声形态由上方 digit_leading_noise 的无 CJK 条件精确拦截）。
        starts_lower = bool(
            _re_mod.match(r"^[a-z]", ln["text"].strip())
        )
        ends_period = ln["text"].rstrip().endswith((".", "。", ";", "；"))
        # 缺字符号（cid:N）密集的行是公式/特殊字体重排区，不是标题
        cid_heavy = ln["text"].count("(cid:") >= 1
        # 注意优先级：by_pattern（编号标题）是精确信号——句式/词性防御
        # 只约束 by_size（字号启发式），不否决编号命中（否则"1.1 研究
        # 背景"被数字开头防御误伤，验收 v9 回归发现）。
        if (is_caption or dense_frags or not text_is_wordy
                or starts_lower or ends_period or cid_heavy):
            by_size = False
        if is_caption or dense_frags or cid_heavy:
            by_pattern = False
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
    text_blocks = [
        _line_block(ln, modal_size, container_ref, doc_heading_sizes)
        for ln in [*heading_lines, *paragraphs]
    ]

    ordered = _sort_by_top(text_blocks + table_blocks)
    return ordered, []


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

    整改轮 P-2 附带：text 回退（第 3/4 层）只在页面存在**多片段行**
    （列对齐信号）时启用——纯散文页（每行单片段）不进 text 回退，
    否则词距会被切成 2 列假表（"3D|Printing" 形态）。
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
    if not _page_has_multifragment_lines(page):
        return []  # 无列对齐信号：text 回退只会产假表
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


def _page_has_multifragment_lines(page: Any) -> bool:
    """页面是否存在多片段行（列对齐信号，text 回退的前置门槛）.

    按**原始 top 带**计算（v8 的 x 拆分会把同 top 的列内容拆成独立
    行，逐行判定永远只见 1 片段）——同 top 带的全部字符一起做片段数。
    """
    try:
        visible = [
            c for c in page.chars if (c.get("text") or "").strip()
        ]
        visible.sort(key=lambda c: (c["top"], c["x0"]))
        row: list[dict[str, Any]] = []
        anchor: float | None = None
        for ch in visible:
            if anchor is None or abs(ch["top"] - anchor) > LINE_TOP_TOLERANCE:
                if _line_segments(row) >= 2:
                    return True
                row = []
                anchor = ch["top"]
            row.append(ch)
        return _line_segments(row) >= 2
    except Exception:  # noqa: BLE001
        return False


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


def detect_column_split(
    lines: list[dict[str, Any]],
    page_width: float,
    min_lines: int = 8,
) -> tuple[float, list[dict[str, Any]]] | None:
    """双栏检测（通用，验收 v8 + 整改轮 P-1）：(栏边界 x, 通栏行) 或 None.

    判定：以页宽中点做严格二分建立左缘带/右缘带，两带有稳定间隙
    （>=15pt）即为双栏。**通栏行收紧为"跨沟行"**（x0 < 左缘 且
    x1 > 右缘）——左栏略过中线但未跨沟的长行不再被误判通栏锚（整改轮
    P-1：误锚会把右栏内容插到左栏中间）。其余越线行按中心归栏。
    """
    if len(lines) < min_lines:
        return None
    mid = page_width / 2
    strict_left = [ln for ln in lines if ln["bbox"][2] <= mid + 10]
    strict_right = [ln for ln in lines if ln["bbox"][0] >= mid - 10]
    if len(strict_left) < max(2, min_lines // 2) or len(strict_right) < max(
        2, min_lines // 2
    ):
        return None
    if len(strict_left) + len(strict_right) < 0.5 * len(lines):
        return None
    left_edge = max(ln["bbox"][2] for ln in strict_left)
    right_edge = min(ln["bbox"][0] for ln in strict_right)
    if right_edge - left_edge < 15:
        return None
    fullwidth = []
    for ln in lines:
        if ln in strict_left or ln in strict_right:
            continue
        x0, _, x1, _ = ln["bbox"]
        if x0 < left_edge and x1 > right_edge:
            fullwidth.append(ln)  # 真跨沟：通栏锚
    return (left_edge + right_edge) / 2, fullwidth


def reorder_columns(
    lines: list[dict[str, Any]], page_width: float
) -> list[dict[str, Any]]:
    """双栏阅读序重排：跨沟通栏行按 top 原位，栏内行先左后右.

    栏归属按行**中心点**（整改轮 P-1）：中心 < 边界归左栏（覆盖左栏
    长行越过中线的情形），>= 边界归右栏。无双栏结构时原样返回。
    """
    detected = detect_column_split(lines, page_width)
    if detected is None:
        return lines
    boundary, fullwidth = detected
    anchors = sorted(fullwidth, key=lambda ln: ln["bbox"][1])
    left = [
        ln for ln in lines
        if ln not in fullwidth
        and (ln["bbox"][0] + ln["bbox"][2]) / 2 < boundary
    ]
    right = [
        ln for ln in lines
        if ln not in fullwidth
        and (ln["bbox"][0] + ln["bbox"][2]) / 2 >= boundary
    ]
    out: list[dict[str, Any]] = []
    anchor_tops = [a["bbox"][1] for a in anchors]
    # 锚点把页面分成带；带内先左后右
    bands = []
    prev_top = 0.0
    for at in anchor_tops + [float("inf")]:
        bands.append((prev_top, at))
        prev_top = at
    for lo, hi in bands:
        band_anchors = [
            a for a in anchors if lo <= a["bbox"][1] < hi
        ]
        band_left = [
            ln for ln in left if lo <= ln["bbox"][1] < hi
        ]
        band_right = [
            ln for ln in right if lo <= ln["bbox"][1] < hi
        ]
        out.extend(
            sorted(band_anchors, key=lambda ln: ln["bbox"][1])
        )
        out.extend(sorted(band_left, key=lambda ln: ln["bbox"][1]))
        out.extend(sorted(band_right, key=lambda ln: ln["bbox"][1]))
    return out


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


def _no_text_layer_block(container_ref: dict[str, Any]) -> BackendBlock:
    """无文本层页的 warning 块：只记录事实，不伪造内容（SRS §7.4）."""
    return BackendBlock(
        block_type="warning",
        text="",
        container_ref=container_ref,
        structure={"reason": "no_text_layer"},
    )


def _modal_size_ratio(chars: list[dict[str, Any]]) -> float:
    """页内字号众数的字符占比（纯度）。Proof 稿/图密集页 <0.5。"""
    if not chars:
        return 0.0
    counter = Counter(round(float(c.get("size") or 0.0), 1) for c in chars)
    return counter.most_common(1)[0][1] / len(chars)


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
    raw_lines: list[list[dict[str, Any]]] = []
    anchor: float | None = None
    for ch in sorted(visible, key=lambda c: (c["top"], c["x0"])):
        if anchor is None or abs(ch["top"] - anchor) > LINE_TOP_TOLERANCE:
            raw_lines.append([])
            anchor = ch["top"]
        raw_lines[-1].append(ch)
    # x 断点拆分（验收 v8，通用）：同一 top 的字符若出现大 x 间隙
    # （> 栏间隙阈值），说明横跨了版面元素——左右双栏同行、行号与
    # 正文、表格外列——拆成独立段，避免阅读序左右粘连。
    lines: list[list[dict[str, Any]]] = []
    for row in raw_lines:
        row.sort(key=lambda c: c["x0"])
        seg: list[dict[str, Any]] = [row[0]]
        for c in row[1:]:
            gap = c["x0"] - seg[-1]["x1"]
            size = max(float(c.get("size") or 0.0), 1.0)
            if gap > max(25.0, 2.0 * size):
                lines.append(seg)
                seg = [c]
            else:
                seg.append(c)
        lines.append(seg)

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


#: 家具判定已整体迁至 ``mining/parse_reconciler``（IR 级文档规则，
#: 整改轮用户指令：跨页/跨元素规则不再放在 adapter）。


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


# 注：``_shrink_table_below_headings``（验收 v4）已死代码化——被数据核心
# 收缩（shrink_bbox_to_data_core + bbox_override 旁路）取代，整改轮删除。


def numbered_heading_level(text: str) -> int | None:
    """中文文档编号标题模式 -> 层级（纯映射规则，验收 v3 + 整改轮 P-2）.

    - ``第X章 …``（X 为中文数字，支持组合如"十二"）-> level 1；
    - ``1.2 …`` / ``1.2.3 …``（1-4 级点分编号 + 空格 + 标题文字）->
      编号段数 + 1。**纯拉丁标题必须以大写开头**（整改轮：拦截
      "0.4 nile" 类轴刻度被编号模式误升标题；真实英文标题首字母
      大写是排版惯例）；
    - 其余 -> None。
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
    m2 = re.match(
        r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s+([一-鿿A-Za-z][^。；，！？]{1,40})$",
        s,
    )
    if m2:
        title = m2.group(2)
        if not _re_mod.search(r"[一-鿿]", title) and not title[0].isupper():
            return None  # 纯拉丁小写开头：轴刻度/杂讯（整改轮 P-2）
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
    收缩结果经旁路传入）。整改轮 P-4：**cell 内容与收缩框一致**——
    中心落在收缩框外的网格组被剔除，行列号紧凑重排。
    """
    structure = _table_structure(table, core=bbox_override)
    box = bbox_override or tuple(float(v) for v in table.bbox)
    return BackendBlock(
        block_type="table",
        text=_table_text(structure),
        container_ref={"container_type": "page", "index": page_index},
        bbox=tuple(float(v) for v in box),
        native_ref={"page": page_index, "table_index": table_index},
        structure=structure,
    )


def _table_structure(
    table: Any, core: tuple | None = None
) -> dict[str, Any]:
    """rows[i].cells[j] 坐标网格 + extract() 文本 -> 行列/单元格/span.

    pdfplumber 中合并单元格表现为多个网格位置共享同一 bbox，且
    ``extract()`` 在被覆盖位置返回 None；相同 bbox 的网格位置分组合并
    为一个逻辑单元格，span 由该组在网格上的行列延展推出。

    整改轮 P-4：``core``（收缩框）给出时，组与框无实质垂直重叠的 cell
    剔除，行列号按保留网格紧凑重排——报告的 bbox 与 cell 内容覆盖一致。
    每 cell 携带自身 bbox（供 cell 级 EvidenceSpan）与 ``evidence_index``。
    """
    rows = list(table.rows)
    text_grid = table.extract() or []

    groups: dict[tuple[float, ...], list[tuple[int, int]]] = {}
    for i, row in enumerate(rows):
        for j, bbox in enumerate(row.cells or []):
            if bbox is None:
                continue
            groups.setdefault(tuple(float(v) for v in bbox), []).append((i, j))

    kept: list[tuple[tuple[float, ...], list[tuple[int, int]]]] = []
    for group_bbox, positions in groups.items():
        if core is not None:
            # 垂直重叠判定（整改轮 P-4 修正）：收缩顶边取的是字符 top，
            # 而单元格 bbox 向上含行距/框线 padding——首数据行 cell 会
            # 略高于收缩线。用"有实质重叠"（>2pt）代替中心点在框内。
            overlap = (
                group_bbox[3] > core[1] + 2
                and group_bbox[1] < core[3] - 2
                and group_bbox[2] > core[0]
                and group_bbox[0] < core[2]
            )
            if not overlap:
                continue  # 收缩框外：与报告 bbox 不一致，剔除（P-4）
        kept.append((group_bbox, positions))

    if not kept:
        return {"rows": 0, "cols": 0, "cells": []}

    # 幻影空行/空列剔除（整改轮 P-4 附带）：text 策略按文本行切 row，
    # 字号差异的行间空洞会成为"整行全空"的幻影组——剔除整行全空的
    # 组合（真实稀疏表的空格总有同行非空邻居，不受影响）。
    def _text_at(i0: int, j0: int) -> str:
        if i0 < len(text_grid) and j0 < len(text_grid[i0]):
            return str(text_grid[i0][j0] or "").strip()
        return ""

    kept = [
        (group_bbox, positions)
        for group_bbox, positions in kept
        if _text_at(
            min(i for i, _ in positions), min(j for _, j in positions)
        )
    ]
    if not kept:
        return {"rows": 0, "cols": 0, "cells": []}

    # 紧凑重排行列号（被剔除的行/列不留空洞）
    kept_rows = sorted({i for _, positions in kept for i, _ in positions})
    kept_cols = sorted({j for _, positions in kept for _, j in positions})
    row_map = {i: new for new, i in enumerate(kept_rows)}
    col_map = {j: new for new, j in enumerate(kept_cols)}

    cells: list[dict[str, Any]] = []
    for k, (group_bbox, positions) in enumerate(kept):
        i0 = min(i for i, _ in positions)
        j0 = min(j for _, j in positions)
        row_span = max(i for i, _ in positions) - i0 + 1
        col_span = max(j for _, j in positions) - j0 + 1
        cells.append({
            "row_index": row_map[i0],
            "column_index": col_map[j0],
            "row_span": row_span,
            "column_span": col_span,
            "text": _text_at(i0, j0),
            "bbox": [group_bbox[0], group_bbox[1], group_bbox[2], group_bbox[3]],
            "evidence_index": k,
        })
    cells.sort(key=lambda c: (c["row_index"], c["column_index"]))
    for k, cell in enumerate(cells):
        cell["evidence_index"] = k  # 排序后重编证据索引
    return {
        "rows": len(kept_rows),
        "cols": len(kept_cols),
        "cells": cells,
    }


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
