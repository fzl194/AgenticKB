"""Segment Compiler（M5，SRS §4.12 / §C11 / §3.10 / §5.3）.

把知识快照的 Parse IR **编译**为面向检索的切片视图——只读 element
graph，不重读原文件（切片策略升级 → 复用 IR 重切，不重新解析，A08）。

编译规则（结构边界优先，token 只是上限，SRS §3.7）：

1. **标题链**：heading 维护层级栈；每个切片携带祖先链
   ``((level, title), ...)``（检索命中显示"章 > 节 > …"）；所在节最内层
   标题文本并入首条切片正文（§5.3「heading + paragraphs 编译」）。
2. **段落合并**：同标题链下相邻文本元素在 ``max_tokens`` 内合并；
   单体超限按字符边界二分（``char_range`` 留痕，§1.2「不复制或覆盖
   单一 offsets」的修复）。
3. **表格类型化**（§C11 typed strategy）：
   - 整表一条（``table``，正文 = 统一渲染文本）；
   - ``rows``/``both`` 视图另产逐数据行切片（``table_row``，携带
     表头数组 + 表名 caption + 行号；映射到该行 cell 的证据 span）。
4. **图文**：figure 连同绑定的 caption 编译为独立切片。
5. **家具过滤**：页眉/页脚/页码不进知识切片（Reconciler 已定型，
   切片层消费结论）。

token 计数为字符近似（CJK 场景 1 字 ≈ 1 token；不引 tokenizer 依赖，
策略阈值语义为"字符上限"）。

设计（ADR-0003 D-001）：纯函数，无 IO；输出
``CompiledSegment``（字段语义对齐 ``RawSegmentData`` 兼容投影）。
"""
from __future__ import annotations

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

_TABLE_WHOLE = "table"
_TABLE_ROW = "table_row"


def compile_segments(
    doc: ParsedDocument, policy: SegmentPolicy
) -> tuple[CompiledSegment, ...]:
    """IR + 策略 -> 切片元组（阅读序；纯函数）."""
    caption_of = _caption_map(doc)
    assets = doc.structured_assets
    segments: list[CompiledSegment] = []
    stack: list[tuple[int, str]] = []  # (level, title)
    buffer: list[Element] = []
    buffer_heading_text: str | None = None
    buffer_heading_el: Element | None = None

    def flush() -> None:
        nonlocal buffer, buffer_heading_text, buffer_heading_el
        if buffer:
            segments.extend(
                _emit_merged(buffer, tuple(stack), buffer_heading_text, policy)
            )
            buffer_heading_text = None  # 只在实际产出后清除（空 flush 保标题）
        elif buffer_heading_el is not None and buffer_heading_text:
            # 纯标题节（无正文）：标题文本本身是可检索内容，独立成段。
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
            segments.extend(_emit_table(element, assets, caption_of, policy))
            continue

        if etype == "figure":
            flush()
            seg = _emit_figure(element, caption_of, policy)
            if seg is not None:
                segments.append(seg)
            continue

        if etype == "caption":
            # 已被表格/图绑定的 caption 随主元素编译；游离 caption 按文本处理。
            if element.element_id in caption_of:
                continue

        text = element.text.strip()
        if not text:
            continue
        # 合并判断：同链 + 开关 + 上限内。
        current_len = sum(len(e.text) for e in buffer)
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
                segments.extend(
                    _emit_split(element, tuple(stack), policy)
                )

    flush()
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
        )
        for i, s in enumerate(segments)
    )


# ---------------------------------------------------------------------------
# 内部：标题层级 / caption 绑定
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
) -> list[CompiledSegment]:
    parts = [e.text.strip() for e in elements if e.text.strip()]
    if not parts:
        return []
    if leading_heading and parts and not parts[0].startswith(leading_heading):
        parts[0] = f"{leading_heading}\n{parts[0]}"
    raw_text = "\n".join(parts)
    block_type = elements[0].element_type if elements[0].element_type in (
        _TEXTUAL_TYPES
    ) else elements[0].element_type
    return [CompiledSegment(
        segment_index=-1,  # 占位，由外层重编号
        block_type=block_type,
        raw_text=raw_text,
        heading_chain=chain,
        element_ids=tuple(e.element_id for e in elements),
        links=tuple(_link(e) for e in elements),
        metadata={},
    )]


def _emit_split(
    element: Element, chain: tuple[tuple[int, str], ...], policy: SegmentPolicy
) -> list[CompiledSegment]:
    """单体超限：按字符边界二分（char_range 留痕）."""
    text = element.text
    out: list[CompiledSegment] = []
    start = 0
    while start < len(text):
        end = min(start + policy.max_tokens, len(text))
        out.append(CompiledSegment(
            segment_index=-1,
            block_type=element.element_type,
            raw_text=text[start:end],
            heading_chain=chain,
            element_ids=(element.element_id,),
            links=(_link(element, char_range=(start, end)),),
            metadata={"split": True},
        ))
        start = end
    return out


def _emit_table(
    element: Element,
    assets: dict,
    caption_of: dict[str, str],
    policy: SegmentPolicy,
) -> list[CompiledSegment]:
    asset = assets.get(f"{element.element_id}-table")
    caption = caption_of.get(element.element_id, "")
    base_link = _link(element)
    out = [CompiledSegment(
        segment_index=-1,
        block_type=_TABLE_WHOLE,
        raw_text=element.text,
        heading_chain=(),
        element_ids=(element.element_id,),
        links=(base_link,),
        metadata={
            "table_caption": caption,
            "rows": asset.rows if asset else None,
            "columns": asset.columns if asset else None,
        },
    )]
    if policy.table_view not in ("rows", "both") or asset is None:
        return out

    header_texts, header_rows = _header_of(asset)
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
        out.append(CompiledSegment(
            segment_index=-1,
            block_type=_TABLE_ROW,
            raw_text="\t".join(c.text for c in cells),
            heading_chain=(),
            element_ids=(element.element_id,),
            links=(SegmentElementLink(
                element_id=element.element_id,
                evidence_span_ids=span_ids or base_link.evidence_span_ids,
            ),),
            metadata={
                "table_header": header_texts,
                "table_caption": caption,
                "row_index": row_index,
            },
        ))
    return out


def _header_of(asset: TableAsset) -> tuple[list[str], set[int]]:
    """首表头行的文本数组 + 全部表头行号集合."""
    header_rows = {c.row_index for c in asset.cells if c.is_header}
    texts: list[str] = []
    if header_rows:
        first = min(header_rows)
        texts = [
            c.text for c in sorted(
                (c for c in asset.cells if c.row_index == first),
                key=lambda c: c.column_index,
            )
        ]
    return texts, header_rows


def _emit_figure(
    element: Element, caption_of: dict[str, str], policy: SegmentPolicy
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
        heading_chain=(),
        element_ids=(element.element_id,),
        links=(_link(element),),
        metadata={"figure_caption": caption},
    )


__all__ = ["compile_segments"]
