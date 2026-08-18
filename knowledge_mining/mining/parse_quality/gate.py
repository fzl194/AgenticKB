"""Parse Quality Gate（SRS §C09 / §4.9，整改轮最小实现）.

决策语义（SRS §4.9 表）：
- ``PASS``：允许继续（编译切片 / 固化 Snapshot）；
- ``WARN``：允许继续但保留质量警告（本最小实现不自动触发 REPAIR/
  FALLBACK——那是 Parse Operator 的预算控制，M4 全量实现）；
- ``FAIL``：停止，不产生 Snapshot。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from knowledge_mining.mining.parse_quality.metrics import QualityMetrics


@dataclass(frozen=True)
class QualityIssue:
    """一条门禁问题（stable code + 人读信息）."""

    code: str
    message: str


@dataclass(frozen=True)
class QualityDecision:
    """门禁结论."""

    decision: str  # "PASS" | "WARN" | "FAIL"
    issues: tuple[QualityIssue, ...] = ()
    metrics: QualityMetrics | None = None


@dataclass(frozen=True)
class QualityProfile:
    """质量阈值（domain 可覆写；SRS §4.5 quality profile）."""

    min_char_coverage: float = 0.85
    min_evidence_locatability: float = 0.80
    min_table_cell_evidence: float = 0.30
    min_reading_order_monotonicity: float = 0.60
    warn_char_coverage: float = 0.95


class QualityGate:
    """指标 -> 决策（纯函数式，无 IO）."""

    def __init__(self, profile: QualityProfile | None = None) -> None:
        self.profile = profile or QualityProfile()

    def evaluate(self, metrics: QualityMetrics) -> QualityDecision:
        issues: list[QualityIssue] = []
        decision = "PASS"

        if metrics.element_count == 0:
            return QualityDecision(
                decision="FAIL",
                issues=(QualityIssue(
                    code="empty_document",
                    message="no elements produced by parse",
                ),),
                metrics=metrics,
            )

        if (
            metrics.char_coverage is not None
            and metrics.char_coverage < self.profile.min_char_coverage
        ):
            decision = "FAIL"
            issues.append(QualityIssue(
                code="low_char_coverage",
                message=(
                    f"char coverage {metrics.char_coverage:.2f} < "
                    f"{self.profile.min_char_coverage:.2f}"
                ),
            ))
        elif (
            metrics.char_coverage is not None
            and metrics.char_coverage < self.profile.warn_char_coverage
        ):
            decision = max(decision, "WARN") if decision != "FAIL" else decision
            if decision != "FAIL":
                decision = "WARN"
            issues.append(QualityIssue(
                code="char_coverage_below_target",
                message=(
                    f"char coverage {metrics.char_coverage:.2f} < "
                    f"{self.profile.warn_char_coverage:.2f} (target)"
                ),
            ))

        if metrics.evidence_locatability < self.profile.min_evidence_locatability:
            if decision == "PASS":
                decision = "WARN"
            issues.append(QualityIssue(
                code="low_evidence_locatability",
                message=(
                    f"evidence locatability {metrics.evidence_locatability:.2f} "
                    f"< {self.profile.min_evidence_locatability:.2f}"
                ),
            ))

        if (
            metrics.table_cell_evidence is not None
            and metrics.table_cell_evidence
            < self.profile.min_table_cell_evidence
        ):
            if decision == "PASS":
                decision = "WARN"
            issues.append(QualityIssue(
                code="low_table_cell_evidence",
                message=(
                    f"table cell evidence {metrics.table_cell_evidence:.2f} "
                    "— cells lack independent source spans"
                ),
            ))

        if (
            metrics.reading_order_monotonicity is not None
            and metrics.reading_order_monotonicity
            < self.profile.min_reading_order_monotonicity
        ):
            if decision == "PASS":
                decision = "WARN"
            issues.append(QualityIssue(
                code="reading_order_regressions",
                message=(
                    f"reading order monotonicity "
                    f"{metrics.reading_order_monotonicity:.2f} "
                    f"< {self.profile.min_reading_order_monotonicity:.2f}"
                ),
            ))

        return QualityDecision(
            decision=decision,
            issues=tuple(issues),
            metrics=metrics,
        )


__all__ = [
    "QualityDecision",
    "QualityGate",
    "QualityIssue",
    "QualityProfile",
]
