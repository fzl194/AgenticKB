"""Parse Quality Gate（C09 最小实现）+ 六类指标测试 —— 整改轮，先 RED.

用户指令的指标口径（禁止只报元素数量）：
  字符覆盖率 / 结构准确率 / 表格完整率 / 证据可定位率 / 阅读序正确率 /
  warning 分布 + PASS/WARN/FAIL 决策。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.contracts.parse_ir import (
    Container,
    Element,
    EvidenceSpan,
    ParseIdentity,
    ParsedDocument,
    TableAsset,
    TableCell,
)
from knowledge_mining.mining.parse_quality import (
    QualityGate,
    QualityProfile,
    compute_metrics,
    quality_profile_for,
)

RAW_HASH = "7e" * 32


def _doc(
    elements: tuple[Element, ...],
    *,
    containers: tuple[Container, ...] = (),
    assets: dict | None = None,
    warnings: tuple[str, ...] = (),
) -> ParsedDocument:
    from knowledge_mining.mining.contracts.parse_ir import Diagnostics

    return ParsedDocument(
        schema_version="0.1",
        source_identity=ParseIdentity(source_raw_hash=RAW_HASH, parser_fingerprint="p"),
        containers=containers or (Container(
            container_id="c-doc", container_type="section", order_index=0,
        ),),
        elements=elements,
        structured_assets=assets or {},
        diagnostics=Diagnostics(warnings=warnings),
    )


def _el(
    eid: str, etype: str, text: str, *, span: bool = True, page: str | None = None
) -> Element:
    return Element(
        element_id=eid,
        element_type=etype,
        order_index=int(eid.split("-")[-1]),
        text=text,
        page_span_ids=(page,) if page else (),
        source_spans=(EvidenceSpan(
            span_id=f"{eid}-s0", raw_text=text or None,
        ),) if span else (),
    )


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------


def test_char_coverage_against_source_text() -> None:
    source = "催化剂的制备与性能研究" * 3
    elements = (
        _el("e-0", "heading", "催化剂的制备与性能研究"),
        _el("e-1", "paragraph", "催化剂的制备与性能研究 催化剂的"),
    )
    metrics = compute_metrics(_doc(elements), source_text=source)
    assert metrics.char_coverage > 0.5
    assert metrics.char_coverage <= 1.0


def test_char_coverage_full_when_text_present() -> None:
    source = "完整覆盖的文本内容"
    elements = (_el("e-0", "paragraph", "完整覆盖的文本内容"),)
    metrics = compute_metrics(_doc(elements), source_text=source)
    assert metrics.char_coverage == pytest.approx(1.0)


def test_evidence_locatability() -> None:
    elements = (
        _el("e-0", "paragraph", "有证据", span=True),
        _el("e-1", "paragraph", "无证据", span=False),
    )
    metrics = compute_metrics(_doc(elements))
    assert metrics.evidence_locatability == pytest.approx(0.5)


def test_table_completeness_and_cell_evidence() -> None:
    table = Element(
        element_id="e-0",
        element_type="table",
        order_index=0,
        text="a\tb",
        source_spans=(EvidenceSpan(span_id="e-0-s0", raw_text="a b"),),
    )
    asset = TableAsset(
        table_id="e-0-table",
        page_span_ids=(),
        rows=2,
        columns=2,
        cells=(
            TableCell(row_index=0, column_index=0, text="a",
                      source_span_id="e-0-s0"),
            TableCell(row_index=0, column_index=1, text="b"),  # 无 span
            TableCell(row_index=1, column_index=0, text="",
                      source_span_id=None),  # 空格不计
            TableCell(row_index=1, column_index=1, text="d",
                      source_span_id="e-0-s0"),
        ),
    )
    metrics = compute_metrics(_doc((table,), assets={"e-0-table": asset}))
    assert metrics.table_cell_evidence == pytest.approx(2 / 3)  # 非空 cell 2/3 有 span


def test_structure_accuracy_against_expectations() -> None:
    from knowledge_mining.mining.parse_quality import GoldenExpectations

    elements = (
        _el("e-0", "heading", "第一章 绪论"),
        _el("e-1", "paragraph", "正文。"),
        _el("e-2", "heading", "1.1 背景"),
    )
    exp = GoldenExpectations(
        expected_headings=("第一章 绪论", "1.1 背景", "1.2 意义"),
        expected_paragraph_anchors=("正文",),
    )
    metrics = compute_metrics(_doc(elements), expectations=exp)
    assert metrics.heading_match_ratio == pytest.approx(2 / 3)
    assert metrics.anchor_hit_ratio == pytest.approx(1.0)
    assert metrics.structure_accuracy < 1.0


def test_reading_order_score_with_bboxes() -> None:
    c0 = Container(container_id="c-p0", container_type="page", order_index=0)

    def _boxed(eid: str, order: int, top: float) -> Element:
        return Element(
            element_id=eid, element_type="paragraph", order_index=order,
            text="t", page_span_ids=("c-p0",),
            source_spans=(EvidenceSpan(
                span_id=f"s-{eid}", page_id="c-p0",
                visual_region={"bbox": [72.0, top, 300.0, top + 12.0]},
            ),),
        )

    # 正序：阅读序（order_index）与 top 单调一致
    e1 = _boxed("e-0", 0, 100.0)
    e2 = _boxed("e-1", 1, 200.0)
    metrics = compute_metrics(_doc((e1, e2), containers=(c0,)))
    assert metrics.reading_order_monotonicity == pytest.approx(1.0)
    # 反序：阅读序上 top 回退（200 -> 100）
    e3 = _boxed("e-2", 0, 200.0)
    e4 = _boxed("e-3", 1, 100.0)
    metrics_bad = compute_metrics(_doc((e3, e4), containers=(c0,)))
    assert metrics_bad.reading_order_monotonicity == pytest.approx(0.0)


def test_warning_distribution_counted() -> None:
    elements = (_el("e-0", "paragraph", "x"),)
    metrics = compute_metrics(_doc(
        elements,
        warnings=("image materialization not supported", "chart (2) not supported"),
    ))
    assert metrics.warning_counts.get("image") == 1
    assert metrics.warning_counts.get("chart") == 1


# ---------------------------------------------------------------------------
# 决策
# ---------------------------------------------------------------------------


def test_gate_pass_on_good_document() -> None:
    elements = (
        _el("e-0", "heading", "标题"),
        _el("e-1", "paragraph", "正文内容一段。"),
        _el("e-2", "paragraph", "正文内容二段。"),
    )
    metrics = compute_metrics(
        _doc(elements), source_text="标题正文内容一段。正文内容二段。"
    )
    decision = QualityGate().evaluate(metrics)
    assert decision.decision in ("PASS", "WARN")
    assert not decision.issues


def test_gate_fail_on_empty_document() -> None:
    metrics = compute_metrics(_doc(()))
    decision = QualityGate().evaluate(metrics)
    assert decision.decision == "FAIL"
    assert any(i.code == "empty_document" for i in decision.issues)


def test_gate_warn_on_low_evidence() -> None:
    elements = (
        _el("e-0", "paragraph", "一段", span=False),
        _el("e-1", "paragraph", "二段", span=False),
    )
    metrics = compute_metrics(_doc(elements))
    decision = QualityGate().evaluate(metrics)
    assert decision.decision == "WARN"
    assert any(i.code == "low_evidence_locatability" for i in decision.issues)


def test_profile_thresholds_change_decision() -> None:
    elements = (
        _el("e-0", "paragraph", "一段", span=False),
        _el("e-1", "paragraph", "二段", span=False),
    )
    metrics = compute_metrics(_doc(elements))
    strict = QualityProfile(min_evidence_locatability=1.0)
    decision = QualityGate(profile=strict).evaluate(metrics)
    assert decision.decision in ("WARN", "FAIL")


def test_named_quality_profiles_have_distinct_operational_thresholds() -> None:
    """P09-S2：档位名必须映射为真实阈值，而非仅停留在 UI 参数。"""
    strict = quality_profile_for("strict")
    lenient = quality_profile_for("lenient")

    assert strict.min_char_coverage == pytest.approx(0.95)
    assert strict.warn_char_coverage == pytest.approx(0.99)
    assert strict.min_evidence_locatability == pytest.approx(0.90)
    assert lenient.min_char_coverage == pytest.approx(0.70)
    assert lenient.min_evidence_locatability == pytest.approx(0.0)
    with pytest.raises(ValueError, match="quality profile"):
        quality_profile_for("experimental")
