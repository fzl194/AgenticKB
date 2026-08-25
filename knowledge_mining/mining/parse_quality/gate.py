"""Parse Quality Gate（SRS §C09 / §4.9；M4 补全五值决策 + 预算）.

决策语义（SRS §4.9 表）：
- ``PASS``：允许继续（编译切片 / 固化 Snapshot）；
- ``WARN``：允许继续但保留质量警告；
- ``REPAIR``：只修复指定页/区域，然后重新评估——Gate 产出
  :class:`RepairRequest`（目标容器 + 原因），实际修复执行由 Operator
  决定（M4 无云端修复后端时由编排层降级：转 FALLBACK 或按 WARN 收尾）；
- ``FALLBACK``：使用 Parse Plan 的下一个后端重新执行——Gate 产出
  :class:`FallbackRequest`（原因码；排除已试 parser 由编排层补全）；
- ``FAIL``：停止，不产生 Snapshot。

预算控制（§4.9 防死循环）：传入 :class:`~contracts.parse_plan.AttemptBudget`
与已用次数时，REPAIR/FALLBACK 只在预算内可选；耗尽则退回 FAIL（覆盖类
灾难）或 WARN（可修复类信号仅保留可见性）。

兼容性：``evaluate(metrics)`` 单参调用（M2/整改轮调用方）行为不变——
无预算上下文 = 不做 REPAIR/FALLBACK 升级，硬失败仍 FAIL、空容器仅 WARN。
"""
from __future__ import annotations

from dataclasses import dataclass

from knowledge_mining.mining.contracts.parse_plan import AttemptBudget
from knowledge_mining.mining.parse_quality.metrics import QualityMetrics

#: SRS §4.9 五值决策词表（单一事实源，测试/编排/报告共用）。
QUALITY_DECISIONS: frozenset[str] = frozenset(
    {"PASS", "WARN", "REPAIR", "FALLBACK", "FAIL"}
)


@dataclass(frozen=True)
class QualityIssue:
    """一条门禁问题（stable code + 人读信息）."""

    code: str
    message: str


@dataclass(frozen=True)
class RepairRequest:
    """REPAIR 请求（§4.9：只修复指定页/区域）.

    ``container_ids`` 定位低质容器（来自 ``QualityMetrics.empty_container_ids``）；
    ``reason_codes`` 供审计。修复执行器（页级重解析/VLM）由 Operator 注入，
    Gate 只声明「哪里需要修、为什么」。
    """

    reason_codes: tuple[str, ...]
    container_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FallbackRequest:
    """FALLBACK 请求（§4.9：换 Parse Plan 的下一个后端）.

    ``exclude_parser_ids``：已试过且失败/质量不达标的 parser（编排层补全，
    Gate 只知指标不知 parser 身份）。
    """

    reason_codes: tuple[str, ...]
    exclude_parser_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityDecision:
    """门禁结论（含推荐动作的请求对象；无对应动作时为 None）."""

    decision: str  # QUALITY_DECISIONS 之一
    issues: tuple[QualityIssue, ...] = ()
    metrics: QualityMetrics | None = None
    repair_request: RepairRequest | None = None
    fallback_request: FallbackRequest | None = None


@dataclass(frozen=True)
class QualityProfile:
    """质量阈值（domain 可覆写；SRS §4.5 quality profile）."""

    min_char_coverage: float = 0.85
    min_evidence_locatability: float = 0.80
    min_table_cell_evidence: float = 0.30
    min_reading_order_monotonicity: float = 0.60
    warn_char_coverage: float = 0.95


def quality_profile_for(profile_name: str) -> QualityProfile:
    """将冻结 Plan 的档位名解析为不可变的质量阈值。"""
    if profile_name == "default":
        return QualityProfile()
    if profile_name == "strict":
        return QualityProfile(
            min_char_coverage=0.95,
            warn_char_coverage=0.99,
            min_evidence_locatability=0.90,
        )
    if profile_name == "lenient":
        # lenient 只保留空文档和 <70% 覆盖率两类硬拒绝；其余问题仍可写入
        # metadata，但不应阻断尽量入库的工作流。
        return QualityProfile(
            min_char_coverage=0.70,
            warn_char_coverage=0.70,
            min_evidence_locatability=0.0,
            min_table_cell_evidence=0.0,
            min_reading_order_monotonicity=0.0,
        )
    raise ValueError(f"unknown quality profile {profile_name!r}")


class QualityGate:
    """指标 -> 决策（纯函数式，无 IO；预算为可选上下文）."""

    def __init__(self, profile: QualityProfile | None = None) -> None:
        self.profile = profile or QualityProfile()

    def evaluate(
        self,
        metrics: QualityMetrics,
        *,
        budget: AttemptBudget | None = None,
        backend_attempts_used: int = 0,
        repair_attempts_used: int = 0,
    ) -> QualityDecision:
        """评估指标快照；预算上下文存在时按剩余额度做 REPAIR/FALLBACK 升级."""
        issues: list[QualityIssue] = []
        decision = "PASS"

        if metrics.element_count == 0:
            # 垃圾/空输入：换后端无济于事（A06 语义），恒 FAIL。
            return QualityDecision(
                decision="FAIL",
                issues=(QualityIssue(
                    code="empty_document",
                    message="no elements produced by parse",
                ),),
                metrics=metrics,
            )

        coverage_failed = False
        if (
            metrics.char_coverage is not None
            and metrics.char_coverage < self.profile.min_char_coverage
        ):
            coverage_failed = True
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
            decision = "WARN"
            issues.append(QualityIssue(
                code="char_coverage_below_target",
                message=(
                    f"char coverage {metrics.char_coverage:.2f} < "
                    f"{self.profile.warn_char_coverage:.2f} (target)"
                ),
            ))

        if metrics.evidence_locatability < self.profile.min_evidence_locatability:
            decision = "WARN" if decision == "PASS" else decision
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
            decision = "WARN" if decision == "PASS" else decision
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
            decision = "WARN" if decision == "PASS" else decision
            issues.append(QualityIssue(
                code="reading_order_regressions",
                message=(
                    f"reading order monotonicity "
                    f"{metrics.reading_order_monotonicity:.2f} "
                    f"< {self.profile.min_reading_order_monotonicity:.2f}"
                ),
            ))

        # -- 覆盖类灾难：FAIL，或预算内升级为 FALLBACK（§4.9） ----------------
        if coverage_failed:
            fallback = None
            if (
                budget is not None
                and backend_attempts_used < budget.max_backend_attempts
            ):
                fallback = FallbackRequest(
                    reason_codes=("low_char_coverage",),
                )
            elif budget is not None:
                issues.append(QualityIssue(
                    code="fallback_budget_exhausted",
                    message=(
                        f"backend attempts used {backend_attempts_used}/"
                        f"{budget.max_backend_attempts}; no fallback left"
                    ),
                ))
            if fallback is not None:
                return QualityDecision(
                    decision="FALLBACK",
                    issues=tuple(issues),
                    metrics=metrics,
                    fallback_request=fallback,
                )
            return QualityDecision(
                decision="FAIL",
                issues=tuple(issues),
                metrics=metrics,
            )

        # -- 可修复信号：空容器（低质页）→ 预算内 REPAIR，否则 WARN 可见 -----
        if metrics.empty_container_ids:
            if (
                budget is not None
                and repair_attempts_used < budget.max_repair_attempts
            ):
                return QualityDecision(
                    decision="REPAIR",
                    issues=tuple(issues),
                    metrics=metrics,
                    repair_request=RepairRequest(
                        reason_codes=("empty_containers",),
                        container_ids=metrics.empty_container_ids,
                    ),
                )
            if budget is not None:
                issues.append(QualityIssue(
                    code="repair_budget_exhausted",
                    message=(
                        f"repair attempts used {repair_attempts_used}/"
                        f"{budget.max_repair_attempts}; empty containers "
                        f"{metrics.empty_container_ids} left visible"
                    ),
                ))
            decision = "WARN"

        return QualityDecision(
            decision=decision,
            issues=tuple(issues),
            metrics=metrics,
        )


__all__ = [
    "QUALITY_DECISIONS",
    "FallbackRequest",
    "QualityDecision",
    "QualityGate",
    "QualityIssue",
    "QualityProfile",
    "RepairRequest",
    "quality_profile_for",
]
