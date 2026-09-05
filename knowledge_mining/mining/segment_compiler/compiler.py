"""Segment Compiler（M5，SRS §4.12 / §C11 / §3.10 / §5.3）.

把知识快照的 Parse IR **编译**为面向检索的切片视图——只读 element
graph，不重读原文件（切片策略升级 → 复用 IR 重切，不重新解析，A08）。

编译规则（结构边界优先，token 只是上限，SRS §3.7）：

1. **标题链**：heading 维护层级栈；每个切片携带祖先链
   ``((level, title), ...)``（检索命中显示"章 > 节 > …"）；所在节最内层
   标题文本并入首条切片正文（§5.3「heading + paragraphs 编译」）。
2. **段落合并**：同标题链下相邻文本元素在 ``max_tokens`` 内合并；
   单体超限按**行边界**分组（``char_range`` 留痕，§1.2「不复制或覆盖
   单一 offsets」的修复），尾片不足 ``min_tokens`` 并入前片。
3. **表格类型化**（§C11 typed strategy；v2 对齐工业界"表格原子"惯例）：
   - ``whole``（默认）：**整表一条**——一表一片，永不与正文合并；
   - ``rows``：只产逐数据行切片（自描述行文本：列名=值，带表名/表头
     上下文，不产裸行）；
   - ``both``：整表 + 逐行双视图（structure_json ``view`` 字段区分）；
   - 整表超 ``max_tokens`` 才降级：按**完整数据行分组**、每组重复表头
     前缀（不字符硬切，工业界表格切片标准做法）。
4. **小片治理**（``min_tokens`` 生效，v2）：同章节内不足下限的孤立
   文本片（引导句 / 无正文标题 / 切分尾片）并入相邻切片；紧跟表格的
   引导句并入该表格片作前缀（表格身份不变）。表格/图片为原子单元，
   无论多小不参与合并。v2.1 唯一例外：整节不足一行的**单行样板章节**
   （< 48 token）跨章节按文档顺序并入相邻文本片，消除章节边界外的
   孤立碎片。
5. **图文**：figure 连同绑定的 caption 编译为独立切片。
6. **家具过滤**：页眉/页脚/页码不进知识切片（Reconciler 已定型，
   切片层消费结论）。
7. **语义标注**（v2，零解析成本）：按章节标题模式给每片标
   ``semantic_role``（定义/枚举/例子/结论/约束/导航）；按表头判别表格
   语义类型 ``table_kind``（关系表/定义表/通用表）——给下游挖掘
   pipeline 提供可过滤的结构轴。

token 计数为字符近似（CJK 场景 1 字 ≈ 1 token；不引 tokenizer 依赖，
策略阈值语义为"字符上限"）。

设计（ADR-0003 D-001）：纯函数，无 IO；输出
``CompiledSegment``（字段语义对齐 ``RawSegmentData`` 兼容投影）。
"""
from __future__ import annotations

import re

from knowledge_mining.mining.contracts.parse_ir.types import (
    Element,
    ParsedDocument,
    TableAsset,
)
from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentElementLink,
    SegmentPolicy,
)

#: 不进知识切片的元素类型（家具，Reconciler 已定型）。
_FURNITURE_TYPES = frozenset({
    "page_header", "page_footer", "page_number",
})

#: 按段落规则编译的文本类元素（其余类型原样透传 block_type）。
_TEXTUAL_TYPES = frozenset({
    "paragraph", "list_item", "code", "quote", "footnote", "toc_entry",
    "reference",
})

#: 原子单元：无论多小不与相邻切片合并（工业界表格切片惯例）。
_ATOMIC_TYPES = frozenset({"table", "table_row", "figure"})

#: 单行章节吸收线（v2.1）：整节正文不足一行的"样板章节"（如
#: "应用限制\n本特性无应用限制。"）跨章节并入相邻文本片。低于
#: ``min_tokens`` 但高于此线的节仍有独立检索价值，保持独立。
_MICRO_ABSORB_TOKENS = 48

_TABLE_WHOLE = "table"
_TABLE_ROW = "table_row"

#: semantic_role 标注词表（章节标题模式 -> 角色；按特异性降序匹配）。
_ROLE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("enumeration", ("枚举", "取值", "可选值")),
    ("example", ("例子", "示例", "典型", "案例", "举例")),
    ("conclusion", ("结论", "总结", "建议", "注意")),
    ("constraint", ("边界", "禁止", "约束", "规则", "限制", "关系")),
    ("definition", ("定义", "字段", "schema", "标准定义")),
    ("overview", ("概述", "总体", "结构", "定位", "一句话", "背景")),
)

#: 表格语义类型判别（表头列名集合 -> table_kind）。
_RELATION_HEADER_MARKS = (
    {"起点", "终点"}, {"源", "目标"}, {"from", "to"}, {"source", "target"},
)
_DEFINITION_HEADER_MARKS = (
    "对象", "字段", "参数", "类型", "说明", "中文名", "名称", "命令", "定位",
)


def compile_segments(
    doc: ParsedDocument, policy: SegmentPolicy
) -> tuple[CompiledSegment, ...]:
    """IR + 策略 -> 切片元组（阅读序；纯函数）."""
    caption_of = _caption_map(doc)
    bound_captions = _bound_caption_ids(doc)
    assets = doc.structured_assets
    segments: list[CompiledSegment] = []
    stack: list[tuple[int, str]] = []  # (level, title)
    buffer: list[Element] = []
    buffer_heading_text: str | None = None
    buffer_heading_el: Element | None = None

    def flush() -> None:
        nonlocal buffer, buffer_heading_text, buffer_heading_el
        if buffer:
            segments.extend(_emit_merged(
                buffer, tuple(stack), buffer_heading_text, policy,
                heading_el=buffer_heading_el,
            ))
            buffer_heading_text = None  # 只在实际产出后清除（空 flush 保标题）
        elif buffer_heading_el is not None and buffer_heading_text:
            # 纯标题节（无正文）：标题文本本身是可检索内容，独立成段
            # （小片治理后并入相邻片）。
            segments.append(CompiledSegment(
                segment_index=-1,
                block_type="heading",
                raw_text=buffer_heading_text,
                heading_chain=tuple(stack),
                element_ids=(buffer_heading_el.element_id,),
                links=(_link(buffer_heading_el),),
                metadata={},
            ))
            buffer_heading_text = None
        buffer = []
        buffer_heading_el = None

    for element in doc.elements:
        etype = element.element_type
        if etype in _FURNITURE_TYPES:
            continue

        if etype == "heading":
            flush()
            level = _level_of(element, len(stack))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, element.text.strip()))
            buffer_heading_text = element.text.strip()
            buffer_heading_el = element
            continue

        if etype == "table":
            flush()
            segments.extend(_emit_table(
                element, assets, caption_of, policy, tuple(stack)
            ))
            continue

        if etype == "figure":
            flush()
            seg = _emit_figure(
                element, caption_of, policy, tuple(stack)
            )
            if seg is not None:
                segments.append(seg)
            continue

        if etype == "caption":
            # 已被表格/图绑定的 caption 随主元素编译（表名进元数据）；
            # 游离 caption 按文本处理。
            if element.element_id in bound_captions:
                continue

        text = element.text.strip()
        if not text:
            continue
        # 合并判断：同链 + 开关 + 上限内（对抗评审 HIGH-4：与产出一致，
        # 用 strip 后长度；标题并入首段的长度一并计入）。
        current_len = sum(len(t.strip()) for t in
                          (e.text for e in buffer)) + (
            len(buffer_heading_text) if buffer_heading_text else 0
        )
        if (
            buffer
            and policy.merge_adjacent_paragraphs
            and current_len + len(text) <= policy.max_tokens
        ):
            buffer.append(element)
        else:
            if buffer:
                flush()  # 只在有内容待产出时 flush（空 flush 会误吞待定标题）
            if len(text) <= policy.max_tokens:
                buffer.append(element)
            else:
                # 单体超限：行边界分组切片，继承待定标题到首片——注入首片
                # 正文，并清掉挂起标题（防循环末尾 flush 产出错位的重复标题段）。
                leading = buffer_heading_text
                parts = _emit_split(element, tuple(stack), policy)
                if leading and parts:
                    first_text = parts[0].raw_text
                    # 标题前缀注入首片：标题元素一并进溯源链。
                    head_ids = (
                        (buffer_heading_el.element_id,)
                        if buffer_heading_el is not None else ()
                    )
                    head_links = (
                        (_link(buffer_heading_el),)
                        if buffer_heading_el is not None else ()
                    )
                    parts[0] = CompiledSegment(
                        segment_index=-1,
                        block_type=parts[0].block_type,
                        raw_text=f"{leading}\n{first_text}",
                        heading_chain=parts[0].heading_chain,
                        element_ids=head_ids + parts[0].element_ids,
                        links=head_links + parts[0].links,
                        metadata=parts[0].metadata,
                    )
                segments.extend(parts)
                buffer_heading_text = None
                buffer_heading_el = None

    flush()
    if policy.merge_adjacent_paragraphs:
        segments = _enforce_min(segments, policy)
        segments = _absorb_micro_sections(segments, policy)
    return tuple(
        CompiledSegment(
            segment_index=i,
            block_type=s.block_type,
            raw_text=s.raw_text,
            heading_chain=s.heading_chain,
            element_ids=s.element_ids,
            links=s.links,
            metadata=s.metadata,
            token_count=len(s.raw_text),
            semantic_role=_semantic_role(s),
        )
        for i, s in enumerate(segments)
    )


# ---------------------------------------------------------------------------
# 内部：标题层级 / caption 绑定 / 语义标注
# ---------------------------------------------------------------------------


def _level_of(element: Element, stack_depth: int) -> int:
    level = element.style.get("level")
    return int(level) if isinstance(level, int) and level > 0 else stack_depth + 1


def _caption_map(doc: ParsedDocument) -> dict[str, str]:
    """caption_of 关系 -> {目标元素 id: caption 文本}（Reconciler 绑定）."""
    texts = {e.element_id: e.text.strip() for e in doc.elements}
    mapping: dict[str, str] = {}
    for rel in doc.relations:
        if rel.relation_type == "caption_of":
            caption = texts.get(rel.source_element_id, "")
            if caption:
                mapping[rel.target_element_id] = caption
    return mapping


def _bound_caption_ids(doc: ParsedDocument) -> frozenset[str]:
    """已被 caption_of 绑定的 caption 元素 id（随主元素编译，不再单出片）."""
    return frozenset(
        rel.source_element_id
        for rel in doc.relations
        if rel.relation_type == "caption_of"
    )


def _chain_text(chain: tuple[tuple[int, str], ...]) -> str:
    return "".join(title for _, title in chain)


def _semantic_role(segment: CompiledSegment) -> str:
    """按章节标题模式推导切片语义角色（启发式，零解析成本）."""
    if segment.block_type == "heading":
        return "navigation"
    text = _chain_text(segment.heading_chain).lower()
    for role, marks in _ROLE_PATTERNS:
        if any(mark in text for mark in marks):
            return role
    return "unknown"


def _table_kind(header_texts: list[str]) -> str:
    """按表头列名判别表格语义类型（关系表/定义表/通用表）."""
    headers = {h.strip().lower() for h in header_texts}
    for marks in _RELATION_HEADER_MARKS:
        if marks <= headers:
            return "relation_table"
    if any(mark in headers for mark in _DEFINITION_HEADER_MARKS):
        return "definition_table"
    return "generic_table"


# ---------------------------------------------------------------------------
# 内部：切片产出
# ---------------------------------------------------------------------------


def _link(element: Element, *, char_range: tuple[int, int] | None = None):
    return SegmentElementLink(
        element_id=element.element_id,
        evidence_span_ids=tuple(
            s.span_id for s in element.source_spans if s.span_id
        ),
        char_range=char_range,
    )


def _emit_merged(
    elements: list[Element],
    chain: tuple[tuple[int, str], ...],
    leading_heading: str | None,
    policy: SegmentPolicy,
    *,
    heading_el: Element | None = None,
) -> list[CompiledSegment]:
    parts = [e.text.strip() for e in elements if e.text.strip()]
    if not parts:
        return []
    # 身份取首个内容元素（不受下方前置标题影响——标题是前缀不是类型）。
    block_type = elements[0].element_type
    if leading_heading and parts and not parts[0].startswith(leading_heading):
        parts[0] = f"{leading_heading}\n{parts[0]}"
        # 标题并入首段正文：标题元素也进溯源链（内容在哪，证据就在哪）。
        elements = [heading_el, *elements] if heading_el is not None else elements
    raw_text = "\n".join(parts)
    if len(raw_text) <= policy.max_tokens:
        return [CompiledSegment(
            segment_index=-1,  # 占位，由外层重编号
            block_type=block_type,
            raw_text=raw_text,
            heading_chain=chain,
            element_ids=tuple(e.element_id for e in elements),
            links=tuple(_link(e) for e in elements),
            metadata={},
        )]
    # 合并后仍超限（merge 关闭或标题并入后越界）：退化为行边界分组。
    return _split_text(raw_text, block_type, chain, elements, policy)


def _split_text(
    text: str,
    block_type: str,
    chain: tuple[tuple[int, str], ...],
    elements: list[Element],
    policy: SegmentPolicy,
    *,
    metadata: dict | None = None,
) -> list[CompiledSegment]:
    """行边界分组：每片累计完整行至 ``max_tokens``；单行超限退化为
    字符边界子块（无换行的长文本仍受上限约束）；尾片不足
    ``min_tokens`` 并入前片（容忍少量越界，消灭无上下文残片）。"""
    # 先切出 ≤max 的原子块（完整行优先，超长行字符二分），带 char 偏移。
    chunks: list[tuple[int, int]] = []  # (start, end) 闭开区间
    pos = 0
    for line in text.split("\n"):
        line_end = pos + len(line)
        if len(line) <= policy.max_tokens:
            chunks.append((pos, line_end))
        else:
            s = pos
            while s < line_end:
                chunks.append((s, min(s + policy.max_tokens, line_end)))
                s += policy.max_tokens
        pos = line_end + 1  # 越过换行符

    # 相邻块聚组：组内总长（含连接换行）不超 max。
    groups: list[tuple[int, int]] = []
    for start, end in chunks:
        if groups:
            prev_start, prev_end = groups[-1]
            joined_len = (end - prev_start)  # 组文本连续覆盖原区间
            if joined_len <= policy.max_tokens:
                groups[-1] = (prev_start, end)
                continue
        groups.append((start, end))

    # 尾片并入：最后一片太短且并入后不超 max+min（硬保护），贴给前一片。
    if len(groups) > 1:
        start, end = groups[-1]
        prev_start, prev_end = groups[-2]
        if (end - start) < policy.min_tokens and (
            end - prev_start
        ) <= policy.max_tokens + policy.min_tokens:
            groups[-2] = (prev_start, end)
            groups.pop()

    out: list[CompiledSegment] = []
    for start, end in groups:
        out.append(CompiledSegment(
            segment_index=-1,
            block_type=block_type,
            raw_text=text[start:end],
            heading_chain=chain,
            element_ids=tuple(e.element_id for e in elements),
            links=tuple(
                _link(e, char_range=(start, min(end, len(e.text))))
                if len(elements) == 1 else _link(e)
                for e in elements
            ),
            metadata={"split": True, **(metadata or {})},
        ))
    return out


def _emit_split(
    element: Element, chain: tuple[tuple[int, str], ...], policy: SegmentPolicy
) -> list[CompiledSegment]:
    """单体超限：行边界分组（char_range 留痕）."""
    return _split_text(
        element.text, element.element_type, chain, [element], policy,
    )


def _row_text(header_texts: list[str], cells: list) -> str:
    """自描述行文本：列名=值（工业界表格切片惯例——行脱离表头仍有语义）."""
    pairs = [
        f"{header_texts[c.column_index]}={c.text}" if c.column_index < len(header_texts)
        else c.text
        for c in cells
    ]
    return "；".join(pairs)


def _header_line(header_texts: list[str]) -> str:
    return "\t".join(header_texts)


def _emit_table(
    element: Element,
    assets: dict,
    caption_of: dict[str, str],
    policy: SegmentPolicy,
    chain: tuple[tuple[int, str], ...] = (),
) -> list[CompiledSegment]:
    asset = assets.get(f"{element.element_id}-table")
    caption = caption_of.get(element.element_id, "")
    base_link = _link(element)
    header_texts, header_rows = (
        _header_of(asset) if asset is not None else ([], set())
    )
    # 29号 R02：重复表头确定性消歧（name、name → name、name#2）——
    # 结构化查询的 JSON pivot 与 cells 检索以列名为键，重名会静默覆盖丢列。
    header_texts = _dedup_headers(header_texts)
    kind = _table_kind(header_texts)
    # 29号 R02：稳定表标识从 ParseIR TableAsset 传播——whole/rows/table
    # node/structured asset/cells/target/container 共享同一 table_ref
    # （此前各 segment 回落 tbl:{segment_index}，一张表被拆成多个身份）。
    table_ref = asset.table_id if asset is not None else element.element_id

    out: list[CompiledSegment] = []
    if policy.table_view in ("whole", "both"):
        if asset is None or len(element.text) <= policy.max_tokens:
            out.append(CompiledSegment(
                segment_index=-1,
                block_type=_TABLE_WHOLE,
                raw_text=element.text,
                heading_chain=chain,
                element_ids=(element.element_id,),
                links=(base_link,),
                metadata={
                    "view": "whole",
                    "table_ref": table_ref,
                    "table_header": header_texts,
                    "table_caption": caption,
                    "table_kind": kind,
                    "rows": asset.rows if asset else None,
                    "columns": asset.columns if asset else None,
                },
            ))
        else:
            # 超限整表降级：按完整数据行分组，每组重复表头前缀（不字符硬切）。
            out.extend(_emit_table_row_groups(
                element, asset, header_texts, header_rows, caption, kind,
                policy, chain,
            ))
    if policy.table_view in ("rows", "both") and asset is not None:
        for row_index in range(asset.rows):
            if row_index in header_rows:
                continue
            cells = [
                c for c in asset.cells
                if c.row_index == row_index and c.text.strip()
            ]
            if not cells:
                continue
            span_ids = tuple(
                c.source_span_id for c in cells if c.source_span_id
            )
            prefix = f"[{caption}] " if caption else ""
            # 29号 R02：精确 cell 事实随行传播（列名=值 对）——下游结构面
            # 不再从展示字符串反解析（值含 ；/= 或 caption 前缀时会丢列）。
            # 36号根因 6：表头外列（横幅表头/表头短于网格）必须携带真实
            # 列号三元组 [name, value, column_index]——此前列号在兜底名
            # col{N} 处丢失，结构投影按名反查全部塌缩为 -1，同行 ≥2 个
            # 即撞 asset_table_cells_staging 主键，表单类文档整篇崩溃。
            row_cells = [
                [
                    header_texts[c.column_index]
                    if c.column_index < len(header_texts)
                    else f"col{c.column_index}",
                    c.text,
                    c.column_index,
                ]
                for c in sorted(cells, key=lambda c: c.column_index)
            ]
            out.append(CompiledSegment(
                segment_index=-1,
                block_type=_TABLE_ROW,
                raw_text=prefix + _row_text(header_texts, cells),
                heading_chain=chain,
                element_ids=(element.element_id,),
                links=(SegmentElementLink(
                    element_id=element.element_id,
                    evidence_span_ids=span_ids or base_link.evidence_span_ids,
                ),),
                metadata={
                    "view": "row",
                    "table_ref": table_ref,
                    "table_header": header_texts,
                    "table_caption": caption,
                    "table_kind": kind,
                    "row_index": row_index,
                    "row_cells": row_cells,
                },
            ))
    return out


def _emit_table_row_groups(
    element: Element,
    asset: TableAsset,
    header_texts: list[str],
    header_rows: set[int],
    caption: str,
    kind: str,
    policy: SegmentPolicy,
    chain: tuple[tuple[int, str], ...],
) -> list[CompiledSegment]:
    """超限整表 -> 表头前缀 + 完整数据行分组（每组重复表头）."""
    header = _header_line(header_texts)
    prefix = (f"[{caption}]\n" if caption else "") + header
    rows_by_index: dict[int, list] = {}
    for c in asset.cells:
        if c.row_index not in header_rows and c.text.strip():
            rows_by_index.setdefault(c.row_index, []).append(c)

    groups: list[list[str]] = []
    current: list[str] = []
    current_len = len(prefix)
    for row_index in sorted(rows_by_index):
        line = "\t".join(
            c.text for c in sorted(
                rows_by_index[row_index], key=lambda c: c.column_index,
            )
        )
        if current and current_len + len(line) + 1 > policy.max_tokens:
            groups.append(current)
            current, current_len = [], len(prefix)
        current.append(line)
        current_len += len(line) + 1
    if current:
        groups.append(current)

    return [
        CompiledSegment(
            segment_index=-1,
            block_type=_TABLE_WHOLE,
            raw_text=f"{prefix}\n" + "\n".join(group),
            heading_chain=chain,
            element_ids=(element.element_id,),
            links=(_link(element),),
            metadata={
                "view": "whole",
                "split": "row_group",
                "table_ref": asset.table_id,
                "table_header": header_texts,
                "table_caption": caption,
                "table_kind": kind,
                "rows": asset.rows,
                "columns": asset.columns,
                "group_index": gi,
                "group_count": len(groups),
            },
        )
        for gi, group in enumerate(groups)
    ]


def _dedup_headers(headers: list[str]) -> list[str]:
    """29号 R02：重复表头确定性消歧（name、name → name、name#2）。"""
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in headers:
        count = seen.get(name, 0)
        seen[name] = count + 1
        out.append(name if count == 0 else f"{name}#{count + 1}")
    return out


def _header_of(asset: TableAsset) -> tuple[list[str], set[int]]:
    """首表头行按物理列号展开的文本数组 + 全部表头行号集合.

    表单/合并单元格会让首表头行稀疏；若把 0/2 列压成长度 2 的数组，
    后续按真实 ``column_index`` 取名会把第 1 列错标成第 2 列表头。
    未覆盖列使用稳定的 ``colN`` 名称，既保留列号也让结构化查询可见。
    """
    header_rows = {c.row_index for c in asset.cells if c.is_header}
    texts: list[str] = []
    if header_rows:
        first = min(header_rows)
        cells = sorted(
            (c for c in asset.cells if c.row_index == first),
            key=lambda c: c.column_index,
        )
        width = max(
            int(asset.columns or 0),
            max((int(c.column_index) + 1 for c in cells), default=0),
        )
        texts = [f"col{i}" for i in range(width)]
        for cell in cells:
            index = int(cell.column_index)
            if index >= 0:
                texts[index] = cell.text.strip() or f"col{index}"
    return texts, header_rows


def _emit_figure(
    element: Element,
    caption_of: dict[str, str],
    policy: SegmentPolicy,
    chain: tuple[tuple[int, str], ...] = (),
) -> CompiledSegment | None:
    if not policy.include_figure_captions:
        return None
    caption = caption_of.get(element.element_id, "")
    raw = caption or element.text.strip()
    if not raw:
        return None  # 无 caption 无文本：无可索引内容，不伪造
    return CompiledSegment(
        segment_index=-1,
        block_type="figure",
        raw_text=raw,
        heading_chain=chain,
        element_ids=(element.element_id,),
        links=(_link(element),),
        metadata={"figure_caption": caption},
    )


# ---------------------------------------------------------------------------
# 内部：小片治理（min_tokens 生效）
# ---------------------------------------------------------------------------


def _same_section(a: tuple[tuple[int, str], ...],
                  b: tuple[tuple[int, str], ...]) -> bool:
    """两链同章节：较短链是较长链的前缀（父子章节可并，兄弟章节不并）."""
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def _merge_into(target: CompiledSegment, extra: CompiledSegment) -> CompiledSegment:
    """extra 正文并入 target（溯源合并；身份修正见下）.

    - 纯标题段吸收正文后不再是"标题导航段"——身份升级为正文的类型
      （标题文本转为其前缀，与主循环 heading+paragraph 编译语义一致）；
    - 章节链取两者中更深者（合并段归属最深成员的章节）。
    """
    block_type = (
        extra.block_type if target.block_type == "heading" else target.block_type
    )
    chain = (
        extra.heading_chain
        if len(extra.heading_chain) > len(target.heading_chain)
        else target.heading_chain
    )
    metadata = {} if target.block_type == "heading" else target.metadata
    return CompiledSegment(
        segment_index=-1,
        block_type=block_type,
        raw_text=f"{target.raw_text}\n{extra.raw_text}",
        heading_chain=chain,
        element_ids=target.element_ids + extra.element_ids,
        links=target.links + extra.links,
        metadata=metadata,
    )


def _enforce_min(
    segments: list[CompiledSegment], policy: SegmentPolicy
) -> list[CompiledSegment]:
    """不足 ``min_tokens`` 的孤立文本片并入相邻切片.

    - 表格/行片/图片是原子单元：不吸收邻片，也不被吸收；
    - 紧跟表格的连续小片（引导句/标题）并入该表格片作**前缀**
      （表格身份不变）；
    - 其余小文本片并入前一个非原子片（无前者/跨章节/越界时保序保留）；
    - 合并仅在同章节内进行（结构边界优先），合并后不得超 ``max_tokens``。
    """
    out: list[CompiledSegment] = []
    smalls: list[CompiledSegment] = []  # 连续小片（可能整体并入后邻表格）

    def flush_smalls() -> None:
        for s in smalls:
            if (
                out
                and out[-1].block_type not in _ATOMIC_TYPES
                and _same_section(out[-1].heading_chain, s.heading_chain)
                and len(out[-1].raw_text) + len(s.raw_text) + 1
                <= policy.max_tokens
            ):
                out[-1] = _merge_into(out[-1], s)
            else:
                out.append(s)
        smalls.clear()

    for seg in segments:
        is_atomic = seg.block_type in _ATOMIC_TYPES
        if not is_atomic and len(seg.raw_text) < policy.min_tokens:
            smalls.append(seg)
            continue
        if smalls:
            if seg.block_type in (_TABLE_WHOLE, _TABLE_ROW):
                # 只取与表格同章节的**尾部连续**小片作前缀——更早的
                # 异章节尾段（上一节的收尾段）不阻塞本节引导句并入。
                prefix: list[CompiledSegment] = []
                while smalls and _same_section(
                    smalls[-1].heading_chain, seg.heading_chain
                ):
                    prefix.insert(0, smalls.pop())
                head_text = "\n".join(s.raw_text for s in prefix)
                if (
                    prefix
                    and len(head_text) + len(seg.raw_text) + 1
                    <= policy.max_tokens
                ):
                    # 引导句并入表格：文本前缀，表格身份保留。先保序吐出
                    # 更早的异章节小片再挂表格（否则小片被甩到表格后，
                    # 打乱文档顺序——v2.1 修复）。
                    flush_smalls()
                    out.append(CompiledSegment(
                        segment_index=-1,
                        block_type=seg.block_type,
                        raw_text=f"{head_text}\n{seg.raw_text}",
                        heading_chain=seg.heading_chain,
                        element_ids=tuple(
                            eid for s in prefix for eid in s.element_ids
                        ) + seg.element_ids,
                        links=tuple(
                            lnk for s in prefix for lnk in s.links
                        ) + seg.links,
                        metadata=seg.metadata,
                    ))
                    continue
                smalls.extend(prefix)  # 超限无法前缀：全部保序
            flush_smalls()
        out.append(seg)
    flush_smalls()
    return out


def _absorb_micro_sections(
    segments: list[CompiledSegment], policy: SegmentPolicy
) -> list[CompiledSegment]:
    """单行章节兜底吸收（v2.1，跨章节合并的**唯一**例外）.

    ``_enforce_min`` 以章节为边界，整节不足 ``min_tokens`` 且与前后
    均异章节的"单行样板章节"（特性说明类文档成串出现：对系统的
    影响/应用限制/计费与话单…）会以孤立碎片残留。此 pass 消除它们：

    - 微型文本片（< ``_MICRO_ABSORB_TOKENS``）优先并入**前一个**
      文本片（宿主身份不变；微型节标题已在其正文中，检索不丢信息）；
    - 前方无文本宿主（原子片/文档头）时并入**后邻**片作前缀（顺序
      保持；后邻为表格即"文本前缀 + 表格"形态，表格身份不变）；
    - 连续微型片先互相累积，再整体并入后邻；
    - 越 ``max_tokens`` 或无后邻宿主时保序保留。
    """
    out: list[CompiledSegment] = []
    pend: list[CompiledSegment] = []  # 待并入后邻的微型片（前方是原子片）

    def _flush_pend_into(seg: CompiledSegment) -> CompiledSegment:
        prefix_text = "\n".join(s.raw_text for s in pend)
        merged = CompiledSegment(
            segment_index=-1,
            block_type=seg.block_type,
            raw_text=f"{prefix_text}\n{seg.raw_text}",
            heading_chain=seg.heading_chain,
            element_ids=tuple(
                eid for s in pend for eid in s.element_ids
            ) + seg.element_ids,
            links=tuple(
                lnk for s in pend for lnk in s.links
            ) + seg.links,
            metadata=seg.metadata,
        )
        pend.clear()
        return merged

    for seg in segments:
        is_atomic = seg.block_type in _ATOMIC_TYPES
        is_micro = not is_atomic and len(seg.raw_text) < _MICRO_ABSORB_TOKENS

        if pend:
            if is_micro:
                pend.append(seg)  # 连续微型片：继续累积
                continue
            joined = (
                sum(len(s.raw_text) for s in pend) + len(pend) + len(seg.raw_text)
            )
            if joined <= policy.max_tokens:
                out.append(_flush_pend_into(seg))
            else:  # 超限：微型片保序独立
                out.extend(pend)
                pend.clear()
                out.append(seg)
            continue

        if (
            is_micro
            and out
            and out[-1].block_type not in _ATOMIC_TYPES
            and len(out[-1].raw_text) + len(seg.raw_text) + 1
            <= policy.max_tokens
        ):
            out[-1] = _merge_into(out[-1], seg)
            continue
        if is_micro:
            pend.append(seg)  # 前方无文本宿主：待并入后邻
            continue
        out.append(seg)

    out.extend(pend)  # 尾部微型片无后邻宿主：保序独立
    return out


__all__ = ["compile_segments"]
