"""M4.2 WP8 补全：QualityGate 五值决策 + 修复/回退请求 + 预算（RED 先行）.

SRS §4.9 决策语义：
- REPAIR：只修复指定页/区域，然后重新评估（本 Gate 产出 RepairRequest，
  实际修复执行由 Operator 决定——M4 无云端修复后端时由编排层降级处理）；
- FALLBACK：使用 Parse Plan 的下一个后端重新执行；
- 预算控制：每个 Plan 有最大 backend/repair attempts（§4.9 防死循环）——
  预算耗尽时 REPAIR/FALLBACK 不可选，退回 FAIL/WARN。

兼容性约束：``evaluate(metrics)`` 单参调用（M2/整改轮全部调用方）行为
不变——不传预算 = 不做 REPAIR/FALLBACK 升级，硬失败仍 FAIL。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parse_plan import AttemptBudget
from knowledge_mining.mining.parse_quality.gate import (
    QualityDecision,
    QualityGate,
)
from knowledge_mining.mining.parse_quality.metrics import (
    QualityMetrics,
    compute_metrics,
)

from tests.golden_corpus.corpus import build_corpus, PARSER_ID
from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline


def _metrics(**overrides) -> QualityMetrics:
    defaults = dict(
        element_count=10,
        container_count=3,
        char_coverage=0.99,
        evidence_locatability=0.95,
        reading_order_monotonicity=1.0,
    )
    defaults.update(overrides)
    return QualityMetrics(**defaults)


# ---------------------------------------------------------------------------
# 决策词表
# ---------------------------------------------------------------------------


def test_decision_vocabulary_has_five_values() -> None:
    from knowledge_mining.mining.parse_quality.gate import QUALITY_DECISIONS

    assert QUALITY_DECISIONS == frozenset(
        {"PASS", "WARN", "REPAIR", "FALLBACK", "FAIL"}
    )


# ---------------------------------------------------------------------------
# 预算感知的 FALLBACK
# ---------------------------------------------------------------------------


def test_low_coverage_without_budget_stays_fail() -> None:
    """单参调用（无预算）保持整改轮行为：硬覆盖失败 = FAIL."""
    decision = QualityGate().evaluate(_metrics(char_coverage=0.30))
    assert decision.decision == "FAIL"


def test_low_coverage_with_remaining_budget_becomes_fallback() -> None:
    decision = QualityGate().evaluate(
        _metrics(char_coverage=0.30),
        budget=AttemptBudget(max_backend_attempts=2),
        backend_attempts_used=1,
    )
    assert decision.decision == "FALLBACK"
    assert decision.fallback_request is not None
    assert "low_char_coverage" in decision.fallback_request.reason_codes


def test_low_coverage_with_exhausted_budget_stays_fail() -> None:
    decision = QualityGate().evaluate(
        _metrics(char_coverage=0.30),
        budget=AttemptBudget(max_backend_attempts=2),
        backend_attempts_used=2,
    )
    assert decision.decision == "FAIL"
    codes = [i.code for i in decision.issues]
    assert any("budget" in c for c in codes), codes


def test_empty_document_never_falls_back() -> None:
    """垃圾输入换后端也无济于事——恒 FAIL（A06 语义）."""
    decision = QualityGate().evaluate(
        _metrics(element_count=0, container_count=1, char_coverage=0.0),
        budget=AttemptBudget(max_backend_attempts=3),
        backend_attempts_used=1,
    )
    assert decision.decision == "FAIL"
    assert decision.fallback_request is None


# ---------------------------------------------------------------------------
# 可修复信号：空容器（低质页）
# ---------------------------------------------------------------------------


def test_empty_containers_trigger_repair_request() -> None:
    decision = QualityGate().evaluate(
        _metrics(empty_container_ids=("page-3", "page-7")),
        budget=AttemptBudget(max_repair_attempts=1),
        repair_attempts_used=0,
    )
    assert decision.decision == "REPAIR"
    assert decision.repair_request is not None
    assert decision.repair_request.container_ids == ("page-3", "page-7")


def test_empty_containers_with_exhausted_repair_budget_warn() -> None:
    decision = QualityGate().evaluate(
        _metrics(empty_container_ids=("page-3",)),
        budget=AttemptBudget(max_repair_attempts=1),
        repair_attempts_used=1,
    )
    assert decision.decision == "WARN"
    assert decision.repair_request is None
    codes = [i.code for i in decision.issues]
    assert any("repair" in c for c in codes), codes


def test_empty_containers_without_budget_stay_warn() -> None:
    """无预算上下文（旧调用方）：空容器只产生 WARN 可见性，不升级."""
    decision = QualityGate().evaluate(
        _metrics(empty_container_ids=("page-3",))
    )
    assert decision.decision == "WARN"


# ---------------------------------------------------------------------------
# 回归：好文档不受影响
# ---------------------------------------------------------------------------


def test_good_metrics_still_pass_without_requests() -> None:
    decision = QualityGate().evaluate(
        _metrics(), budget=AttemptBudget(), backend_attempts_used=1,
    )
    assert decision.decision == "PASS"
    assert decision.repair_request is None
    assert decision.fallback_request is None


def test_compute_metrics_reports_empty_container_ids() -> None:
    """指标层补空容器信号（供 REPAIR 定位目标页）."""
    doc = build_corpus()  # 任取一个真实 corpus 文档验证管道
    sample = next(d for d in doc if d.name == "pdf-blank-page")
    parser, normalizer = resolve_pipeline(PARSER_ID[sample.format_key])
    artifact = parser.parse(sample.data, mime=sample.mime)
    ir = normalizer.normalize(artifact, source_raw_hash="m4")
    metrics = compute_metrics(ir)
    # 该样本含一个无文本页；指标应报告出（而非静默）。
    assert isinstance(metrics.empty_container_ids, tuple)
