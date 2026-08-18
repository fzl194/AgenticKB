"""Structural Reconciler（SRS §C08 / §4.8）——文档级结构修复最小实现.

整改轮（2026-08-17）从 native_pdf adapter 迁入/新增四条规则：

- ``furniture_typing``（迁入）：跨容器重复长行 -> page_header/page_footer
  （按容器内垂直位置分侧：上 1/3 header、下 1/3 footer，中部按 header）；
  纯数字/罗马数字短行 -> page_number。只改类型与注记，**不删除内容**。
- ``caption_binding``（新增）：caption 元素（HTML 产）或 "图N/表N/
  Fig.N/Table N" 前缀段落（PDF 形态）与紧邻的下一个表格元素 ->
  ``caption_of`` 关系 + TableAsset.caption_element_id 回填；前缀段落
  升级为 caption 元素。
- ``table_continuation``（新增）：相邻页、列数一致、首行（表头）文本
  相似（完全一致或 Jaccard >= 0.5）-> TableAsset.continuation_of +
  ``continues_on`` 关系。保守：只关联不合并网格。
- ``paragraph_continuation``（新增）：上一页**末**段落与下一页**首**
  段落，栏位 x 范围一致（容差 12pt）、上一段不以句读结尾、下一段
  非列表/标题 -> ``continues_on`` 关系。保守：不改写文本。

所有修复产出 :class:`PatchRecord`（规则名 + element ids + 前后值），
``reconciler_version`` 回写 ParseIdentity 与 diagnostics.backend_provenance。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from knowledge_mining.mining.contracts.parse_ir import (
    Container,
    Element,
    ParsedDocument,
    Relation,
    TableAsset,
)

RECONCILER_VERSION = "structural-reconciler@1"

# 家具判定阈值（自 native_pdf 迁入，语义不变）。
FURNITURE_REPEAT_CONTAINERS = 3
_FURNITURE_MIN_TEXT_LEN = 12
_PAGE_NUMBER_MAX_LEN = 6

_ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
          "xi", "xii", "xiii", "xiv", "xv"}
_PAGE_OF_RE = re.compile(
    r"^\s*-?\s*Page\s+\d+\s+of\s+\d+\s*-?\s*$", re.IGNORECASE
)
# caption 前缀（PDF 形态）：题注不是正文段落。
_CAPTION_PREFIX_RE = re.compile(
    r"^\s*(Fig(ure)?\.?\s*\d|图\s*\d[-−—–.]|Table\s*\d|表\s*\d[-−—–.])"
)
# 段落句读终点（阻断跨页续接）。
_TERMINAL_PUNCT = ("。", ".", "！", "!", "？", "?", "；", ";")
# 栏位 x 容差（pt）。
_COLUMN_X_TOLERANCE = 12.0


@dataclass(frozen=True)
class PatchRecord:
    """一次可追踪的结构修复（SRS §4.8 patch log）."""

    rule: str
    element_ids: tuple[str, ...]
    before: str = ""
    after: str = ""


@dataclass(frozen=True)
class ReconcileResult:
    """Reconciler 输出：修复后的 IR + patch log."""

    document: ParsedDocument
    patches: tuple[PatchRecord, ...]


class StructuralReconciler:
    """文档级结构修复器（SRS §C08）。纯函数式：输入 IR，输出新 IR。"""

    def reconcile(self, doc: ParsedDocument) -> ReconcileResult:
        patches: list[PatchRecord] = []
        elements = list(doc.elements)
        relations = list(doc.relations)
        assets = dict(doc.structured_assets)

        elements, furniture_patches = _rule_furniture_typing(
            elements, doc.containers
        )
        patches.extend(furniture_patches)

        elements, relations, assets, caption_patches = _rule_caption_binding(
            elements, relations, assets
        )
        patches.extend(caption_patches)

        assets, relations, table_patches = _rule_table_continuation(
            elements, assets, relations, doc.containers
        )
        patches.extend(table_patches)

        relations, para_patches = _rule_paragraph_continuation(
            elements, relations, doc.containers
        )
        patches.extend(para_patches)

        reconciled = replace(
            doc,
            elements=tuple(elements),
            relations=tuple(relations),
            structured_assets=assets,
            source_identity=replace(
                doc.source_identity, reconciler_version=RECONCILER_VERSION
            ),
            diagnostics=replace(
                doc.diagnostics,
                backend_provenance={
                    **doc.diagnostics.backend_provenance,
                    "reconciler": RECONCILER_VERSION,
                },
            ),
            metadata={
                **doc.metadata,
                "reconciler_patches": [
                    {"rule": p.rule, "elements": list(p.element_ids)}
                    for p in patches
                ],
            },
        )
        return ReconcileResult(document=reconciled, patches=tuple(patches))


# ---------------------------------------------------------------------------
# R-1 家具标注（自 native_pdf._annotate_furniture/classify_furniture 迁入）
# ---------------------------------------------------------------------------


def _classify_furniture(
    text: str, container_count: int, position: str
) -> str | None:
    """行级家具判定（纯函数，自 native_pdf 迁入 + 位置分侧）."""
    compact_text = (text or "").strip()
    if not compact_text:
        return None
    if len(compact_text) > _FURNITURE_MIN_TEXT_LEN \
            and container_count >= FURNITURE_REPEAT_CONTAINERS:
        if position == "bottom":
            return "page_footer"
        return "page_header"
    compact = compact_text.replace(" ", "")
    if len(compact) <= _PAGE_NUMBER_MAX_LEN and (
        compact.isdigit() or compact.lower() in _ROMAN
    ):
        return "page_number"
    if _PAGE_OF_RE.match(compact_text):
        return "page_number"
    return None


def _rule_furniture_typing(
    elements: list[Element], containers: tuple[Container, ...]
) -> tuple[list[Element], list[PatchRecord]]:
    """跨容器重复文本 -> 家具类型（只改类型与注记，不删除）."""
    if not containers:
        return elements, []
    heights = {
        c.container_id: c.height for c in containers if c.height
    }
    seen: dict[str, set[str]] = {}
    for e in elements:
        if e.element_type != "paragraph" or not e.text.strip():
            continue
        for page_id in e.page_span_ids:
            seen.setdefault(e.text.strip(), set()).add(page_id)

    out: list[Element] = []
    patches: list[PatchRecord] = []
    for e in elements:
        if e.element_type != "paragraph":
            out.append(e)
            continue
        text = e.text.strip()
        container_count = len(seen.get(text, ()))
        position = _vertical_position(e, heights)
        verdict = _classify_furniture(text, container_count, position)
        if verdict is None:
            out.append(e)
            continue
        out.append(replace(
            e,
            element_type=verdict,
            metadata={**e.metadata, "furniture": True},
        ))
        patches.append(PatchRecord(
            rule="furniture_typing",
            element_ids=(e.element_id,),
            before="paragraph",
            after=verdict,
        ))
    return out, patches


def _vertical_position(e: Element, heights: dict[str, float]) -> str:
    """元素在其容器内的垂直位置：top / bottom / middle."""
    for span in e.source_spans:
        if span.visual_region and "bbox" in span.visual_region:
            bbox = span.visual_region["bbox"]
            page_h = heights.get(span.page_id or "")
            if page_h:
                center = (bbox[1] + bbox[3]) / 2
                if center < page_h / 3:
                    return "top"
                if center > page_h * 2 / 3:
                    return "bottom"
            return "middle"
    return "middle"


# ---------------------------------------------------------------------------
# R-2 caption 绑定
# ---------------------------------------------------------------------------


def _rule_caption_binding(
    elements: list[Element],
    relations: list[Relation],
    assets: dict[str, Any],
) -> tuple[list[Element], list[Relation], dict[str, Any], list[PatchRecord]]:
    """caption（或前缀段落）与紧邻下一个表格绑定 caption_of."""
    out = list(elements)
    patches: list[PatchRecord] = []
    for i, e in enumerate(elements):
        is_caption_el = e.element_type == "caption"
        is_prefix_para = (
            e.element_type == "paragraph"
            and bool(_CAPTION_PREFIX_RE.match(e.text.strip()))
        )
        if not (is_caption_el or is_prefix_para):
            continue
        # 紧邻的下一个表格元素（同容器，顺序 +1 起向后找，最多 2 步）
        target = None
        for nxt in elements[i + 1: i + 3]:
            if nxt.element_type == "table" and set(nxt.page_span_ids) & set(
                e.page_span_ids
            ):
                target = nxt
                break
        if target is None:
            continue
        table_id = f"{target.element_id}-table"
        asset = assets.get(table_id)
        if not isinstance(asset, TableAsset):
            continue
        caption_id = e.element_id
        if is_prefix_para:
            out[i] = replace(out[i], element_type="caption")
            patches.append(PatchRecord(
                rule="caption_binding",
                element_ids=(e.element_id,),
                before="paragraph",
                after="caption",
            ))
        assets[table_id] = replace(asset, caption_element_id=caption_id)
        relations.append(Relation(
            source_element_id=caption_id,
            target_element_id=target.element_id,
            relation_type="caption_of",
            method=RECONCILER_VERSION,
        ))
        patches.append(PatchRecord(
            rule="caption_binding",
            element_ids=(caption_id, target.element_id),
            after="caption_of",
        ))
    return out, relations, assets, patches


# ---------------------------------------------------------------------------
# R-3 跨页表格延续
# ---------------------------------------------------------------------------


def _rule_table_continuation(
    elements: list[Element],
    assets: dict[str, Any],
    relations: list[Relation],
    containers: tuple[Container, ...],
) -> tuple[dict[str, Any], list[Relation], list[PatchRecord]]:
    """相邻页 + 列数一致 + 表头相似 -> continuation_of（保守关联）."""
    page_order = [c.container_id for c in sorted(
        containers, key=lambda c: c.order_index
    )]
    page_index = {pid: i for i, pid in enumerate(page_order)}

    tables_by_page: dict[str, list[Element]] = {}
    for e in elements:
        if e.element_type != "table":
            continue
        for pid in e.page_span_ids:
            tables_by_page.setdefault(pid, []).append(e)

    patches: list[PatchRecord] = []
    for pid, tbls in tables_by_page.items():
        idx = page_index.get(pid)
        if idx is None or idx == 0:
            continue
        prev_pid = page_order[idx - 1]
        prev_tables = tables_by_page.get(prev_pid)
        if not prev_tables:
            continue
        prev_last = max(
            prev_tables, key=lambda t: t.order_index
        )
        for cur in sorted(tbls, key=lambda t: t.order_index):
            cur_id = f"{cur.element_id}-table"
            prev_id = f"{prev_last.element_id}-table"
            cur_asset = assets.get(cur_id)
            prev_asset = assets.get(prev_id)
            if not isinstance(cur_asset, TableAsset) \
                    or not isinstance(prev_asset, TableAsset):
                continue
            if cur_asset.columns != prev_asset.columns:
                continue
            if not _header_similar(cur_asset, prev_asset):
                continue
            assets[cur_id] = replace(cur_asset, continuation_of=prev_id)
            relations.append(Relation(
                source_element_id=prev_last.element_id,
                target_element_id=cur.element_id,
                relation_type="continues_on",
                method=RECONCILER_VERSION,
            ))
            patches.append(PatchRecord(
                rule="table_continuation",
                element_ids=(prev_last.element_id, cur.element_id),
                after=f"continuation_of={prev_id}",
            ))
            break  # 每页首个延续表只关联一次
    return assets, relations, patches


def _header_similar(a: TableAsset, b: TableAsset) -> bool:
    """表头行文本相似：完全一致或 Jaccard >= 0.5（保守）."""
    def _header_texts(t: TableAsset) -> list[str]:
        return [
            c.text.strip() for c in t.cells
            if c.is_header and c.text.strip()
        ] or [
            c.text.strip() for c in t.cells
            if c.row_index == 0 and c.text.strip()
        ]

    ha, hb = _header_texts(a), _header_texts(b)
    if not ha or not hb:
        return False
    if ha == hb:
        return True
    set_a, set_b = set(ha), set(hb)
    union = set_a | set_b
    return len(set_a & set_b) / len(union) >= 0.5


# ---------------------------------------------------------------------------
# R-4 跨页段落延续（保守）
# ---------------------------------------------------------------------------


def _rule_paragraph_continuation(
    elements: list[Element],
    relations: list[Relation],
    containers: tuple[Container, ...],
) -> tuple[list[Relation], list[PatchRecord]]:
    """上一页末段 -> 下一页首段：栏位一致 + 无句读终点 -> continues_on."""
    page_order = [c.container_id for c in sorted(
        containers, key=lambda c: c.order_index
    )]
    by_page: dict[str, list[Element]] = {}
    for e in elements:
        if e.element_type != "paragraph" or len(e.page_span_ids) != 1:
            continue
        by_page.setdefault(e.page_span_ids[0], []).append(e)

    patches: list[PatchRecord] = []
    for i in range(len(page_order) - 1):
        prev_pid, cur_pid = page_order[i], page_order[i + 1]
        prev_list = by_page.get(prev_pid)
        cur_list = by_page.get(cur_pid)
        if not prev_list or not cur_list:
            continue
        prev = max(prev_list, key=lambda e: e.order_index)
        cur = min(cur_list, key=lambda e: e.order_index)
        if not prev.text.strip() or not cur.text.strip():
            continue
        if prev.text.rstrip().endswith(_TERMINAL_PUNCT):
            continue
        if not _same_column(prev, cur):
            continue
        relations.append(Relation(
            source_element_id=prev.element_id,
            target_element_id=cur.element_id,
            relation_type="continues_on",
            confidence=0.7,
            method=RECONCILER_VERSION,
        ))
        patches.append(PatchRecord(
            rule="paragraph_continuation",
            element_ids=(prev.element_id, cur.element_id),
            after="continues_on",
        ))
    return relations, patches


def _same_column(a: Element, b: Element) -> bool:
    """两元素的首个 bbox 栏位一致（x0/x1 容差内）。无 bbox 时不判定."""
    ba = _first_bbox(a)
    bb = _first_bbox(b)
    if ba is None or bb is None:
        return False
    return (
        abs(ba[0] - bb[0]) <= _COLUMN_X_TOLERANCE
        and abs(ba[2] - bb[2]) <= _COLUMN_X_TOLERANCE
    )


def _first_bbox(e: Element) -> tuple[float, ...] | None:
    for span in e.source_spans:
        if span.visual_region and "bbox" in span.visual_region:
            return tuple(span.visual_region["bbox"])
    return None


__all__ = [
    "RECONCILER_VERSION",
    "PatchRecord",
    "ReconcileResult",
    "StructuralReconciler",
]
