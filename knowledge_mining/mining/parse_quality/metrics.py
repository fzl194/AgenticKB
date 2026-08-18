"""六类解析质量指标（SRS §C09 / §7.1，整改轮最小实现）.

口径（用户整改指令）：
- ``char_coverage`` 字符覆盖率：源文本（或期望锚文本）中的实义字符
  出现在 IR 元素文本中的比例（多重集合交集 / 源计数）。
- ``structure_accuracy`` 结构准确率：期望标题序列命中率、锚文本命中
  率与（有期望表格网格时的）表格数一致率的综合。
- ``table_cell_evidence`` 表格完整率（证据维度）：非空 TableCell 带独立
  source_span_id 的比例 + 网格一致性（cell 越界即不完整）。
- ``evidence_locatability`` 证据可定位率：至少携带一个有效定位
  （locator 或 raw_text）span 的元素占比。
- ``reading_order_monotonicity`` 阅读序正确率：同容器内相邻元素
  bbox top 单调不回退的比例（双栏切换处天然回退一次，容差 1 次）。
- ``warning_counts`` warning 分布：diagnostics.warnings 的归一化计数。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from knowledge_mining.mining.contracts.parse_ir import (
    Element,
    ParsedDocument,
    TableAsset,
)

# 实义字符：字母数字 + CJK（覆盖率只统计内容字符）。
_MEANINGFUL_RE = re.compile(r"[0-9A-Za-z一-鿿々〆〤]")


@dataclass(frozen=True)
class GoldenExpectations:
    """golden corpus 期望标注（结构准确率的对照输入）."""

    expected_headings: tuple[str, ...] = ()
    expected_paragraph_anchors: tuple[str, ...] = ()
    expected_table_count: int | None = None


@dataclass(frozen=True)
class QualityMetrics:
    """一次解析的六类指标快照."""

    element_count: int = 0
    container_count: int = 0
    char_coverage: float | None = None
    evidence_locatability: float = 0.0
    table_cell_evidence: float | None = None
    table_grid_consistency: float | None = None
    heading_match_ratio: float | None = None
    anchor_hit_ratio: float | None = None
    table_count_match: bool | None = None
    structure_accuracy: float | None = None
    reading_order_monotonicity: float | None = None
    #: 无元素的**叶子**容器（有子容器的结构节点如 workbook 不算）——
    #: 低质页信号，供 QualityGate 产出 REPAIR 请求定位目标（SRS §7.1
    #: empty page ratio / §4.9 REPAIR 指定页）。
    empty_container_ids: tuple[str, ...] = ()
    warning_counts: dict[str, int] = field(default_factory=dict)


def compute_metrics(
    doc: ParsedDocument,
    *,
    source_text: str | None = None,
    expectations: GoldenExpectations | None = None,
) -> QualityMetrics:
    """IR +（可选）源文本/期望标注 -> 指标快照（纯函数）."""
    elements = doc.elements
    ir_text = "".join(e.text for e in elements)

    char_coverage = None
    if source_text is not None:
        char_coverage = _char_coverage(source_text, ir_text)

    # 证据可定位率
    locatable = sum(1 for e in elements if _has_locator(e))
    evidence_locatability = (
        locatable / len(elements) if elements else 0.0
    )

    # 表格指标
    tables = [a for a in doc.structured_assets.values()
              if isinstance(a, TableAsset)]
    table_cell_evidence = None
    table_grid_consistency = None
    if tables:
        non_empty_cells = [
            c for a in tables for c in a.cells if c.text.strip()
        ]
        with_span = sum(1 for c in non_empty_cells if c.source_span_id)
        table_cell_evidence = (
            with_span / len(non_empty_cells) if non_empty_cells else 1.0
        )
        consistent = sum(
            1 for a in tables
            if all(
                c.row_index + c.row_span <= a.rows
                and c.column_index + c.column_span <= a.columns
                for c in a.cells
            )
        )
        table_grid_consistency = consistent / len(tables)

    # 结构准确率（期望对照）
    heading_match_ratio = None
    anchor_hit_ratio = None
    table_count_match = None
    structure_accuracy = None
    if expectations is not None:
        heading_texts = [e.text.strip() for e in elements
                         if e.element_type == "heading"]
        heading_match_ratio = _prefix_match_ratio(
            expectations.expected_headings, heading_texts
        )
        anchor_hit_ratio = (
            sum(
                1 for anchor in expectations.expected_paragraph_anchors
                if any(anchor in e.text for e in elements)
            ) / len(expectations.expected_paragraph_anchors)
            if expectations.expected_paragraph_anchors else None
        )
        if expectations.expected_table_count is not None:
            table_count_match = (
                len(tables) == expectations.expected_table_count
            )
        parts = [r for r in (
            heading_match_ratio, anchor_hit_ratio,
            1.0 if table_count_match is None else
            (1.0 if table_count_match else 0.0),
        ) if r is not None]
        structure_accuracy = (
            sum(parts) / len(parts) if parts else None
        )

    # 阅读序（bbox top 单调性，容器内）
    reading_order = _reading_order_monotonicity(elements)

    return QualityMetrics(
        element_count=len(elements),
        container_count=len(doc.containers),
        char_coverage=char_coverage,
        evidence_locatability=evidence_locatability,
        table_cell_evidence=table_cell_evidence,
        table_grid_consistency=table_grid_consistency,
        heading_match_ratio=heading_match_ratio,
        anchor_hit_ratio=anchor_hit_ratio,
        table_count_match=table_count_match,
        structure_accuracy=structure_accuracy,
        reading_order_monotonicity=reading_order,
        empty_container_ids=_empty_leaf_containers(doc),
        warning_counts=_warning_distribution(doc),
    )


def _char_coverage(source_text: str, ir_text: str) -> float:
    """源实义字符的多重集合覆盖率."""
    src = _MEANINGFUL_RE.findall(source_text)
    if not src:
        return 1.0
    ir_counter = Counter(_MEANINGFUL_RE.findall(ir_text))
    hit = sum(
        min(ir_counter.get(ch, 0), n)
        for ch, n in Counter(src).items()
    )
    return hit / len(src)


def _empty_leaf_containers(doc: ParsedDocument) -> tuple[str, ...]:
    """无元素的叶子容器 id（结构父节点如 workbook 不算空页）.

    元素经 ``page_span_ids`` 绑定容器（历史命名，承载的是容器 id）。
    文档无任何元素-容器绑定时（legacy MD/TXT 形态：无页容器，
    ``page_span_ids`` 恒空）**不做空页判定**——无法区分「容器空」与
    「格式不表达容器归属」，宁缺勿误报。
    """
    parents = {
        c.parent_container_id
        for c in doc.containers
        if c.parent_container_id is not None
    }
    with_elements = {
        cid for e in doc.elements for cid in e.page_span_ids
    }
    if not with_elements:
        return ()
    return tuple(
        c.container_id
        for c in sorted(doc.containers, key=lambda c: c.order_index)
        if c.container_id not in parents
        and c.container_id not in with_elements
    )


def _has_locator(e: Element) -> bool:
    if not e.source_spans:
        return False
    return any(
        s.text_range is not None
        or s.source_locator is not None
        or s.visual_region is not None
        or s.native_ref is not None
        or (s.raw_text or "").strip()
        for s in e.source_spans
    )


def _prefix_match_ratio(expected: tuple[str, ...], actual: list[str]) -> float:
    """期望文本（前缀/归一化）在实际标题中的命中率."""
    if not expected:
        return 1.0
    norm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    actual_norm = [norm(a) for a in actual]
    hit = sum(
        1 for exp in expected
        if any(norm(exp) in a or a.startswith(norm(exp)[:6]) for a in actual_norm)
    )
    return hit / len(expected)


def _reading_order_monotonicity(elements: tuple[Element, ...]) -> float | None:
    """同容器相邻元素 bbox top 不回退比例（None = 无 bbox 证据）."""
    by_page: dict[str, list[float]] = {}
    for e in elements:
        if len(e.page_span_ids) != 1:
            continue
        tops = [
            s.visual_region["bbox"][1]
            for s in e.source_spans
            if s.visual_region and "bbox" in s.visual_region
        ]
        if not tops:
            continue
        by_page.setdefault(e.page_span_ids[0], []).append(
            (e.order_index, min(tops))
        )
    pairs_total = 0
    pairs_ok = 0
    for tops in by_page.values():
        ordered = [t for _, t in sorted(tops)]
        for a, b in zip(ordered, ordered[1:]):
            pairs_total += 1
            if b >= a - 1.0:  # 1pt 容差
                pairs_ok += 1
    if not pairs_total:
        return None
    return pairs_ok / pairs_total


def _warning_distribution(doc: ParsedDocument) -> dict[str, int]:
    """warning 归一化计数（取首词小写，如 image/chart/sheet）."""
    counts: Counter[str] = Counter()
    for w in doc.diagnostics.warnings:
        first = re.match(r"[A-Za-z]+", w)
        if first:
            counts[first.group(0).lower()] += 1
        else:
            counts["other"] += 1
    return dict(counts)


__all__ = [
    "GoldenExpectations",
    "QualityMetrics",
    "compute_metrics",
]
