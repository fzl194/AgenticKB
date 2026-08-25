"""ParsePlan 契约（M4，SRS §4.5 / §4.9 / §C05）.

一次知识更新的解析策略快照：primary backend + **有序** fallback 链 +
尝试预算（防死循环）。SRS §4.9「每个 Parse Plan 有最大 backend attempts、
repair attempts、cost 和总时长」；§2.2「fallback 只能由失败或质量策略
触发，必须留下原因」——原因记录在 attempt 事件（M4.3），Plan 只承载
**允许发生什么**。

与 :class:`~file_inspector.router.RouteDecision` 的关系：RouteDecision 是
路由器的一次决策输出（primary + 候选 fallback + reason codes）；
ParsePlan 在其上补预算与质量档位，是 Operator 执行的**冻结输入**。
转换辅助（avoid contracts -> file_insircle 依赖倒置）由编排层提供。

设计（ADR-0003 D-001）：frozen dataclass + ``__post_init__`` 校验，
纯 stdlib。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 默认计划版本（route policy 版本化属 M3B；先固定哨兵值保证指纹稳定）。
DEFAULT_PLAN_VERSION = "route-policy@1"

#: 默认质量档位名（QualityProfile 由 parse_quality 层解析）。
DEFAULT_QUALITY_PROFILE = "default"

#: 允许写入冻结 ParsePlan 的质量档位；未知值必须在编排前拒绝。
SUPPORTED_QUALITY_PROFILES: frozenset[str] = frozenset(
    {"default", "strict", "lenient"}
)


@dataclass(frozen=True)
class AttemptBudget:
    """一次 Parse Plan 的尝试预算（SRS §4.9 防死循环）.

    - ``max_backend_attempts``：primary + fallback 的**总**尝试上限；
    - ``max_repair_attempts``：局部修复轮次上限（0 = 不修复）；
    - ``max_duration_seconds``：整次知识更新的墙钟时长上限。
    """

    max_backend_attempts: int = 3
    max_repair_attempts: int = 1
    max_duration_seconds: float = 3600.0

    def __post_init__(self) -> None:
        if self.max_backend_attempts < 1:
            raise ValueError(
                f"max_backend_attempts must be >= 1, got "
                f"{self.max_backend_attempts}"
            )
        if self.max_repair_attempts < 0:
            raise ValueError(
                f"max_repair_attempts must be >= 0, got "
                f"{self.max_repair_attempts}"
            )
        if self.max_duration_seconds <= 0:
            raise ValueError(
                f"max_duration_seconds must be > 0, got "
                f"{self.max_duration_seconds}"
            )


@dataclass(frozen=True)
class ParsePlan:
    """一次解析执行的冻结计划（SRS §4.5 ParsePlan）.

    ``fallback_parser_ids`` 是**有序**回退链（越靠前越优先）；链长度不得
    超出 ``budget.max_backend_attempts``——构造即拒绝不可能执行完的计划。
    """

    plan_id: str
    primary_parser_id: str
    fallback_parser_ids: tuple[str, ...] = ()
    budget: AttemptBudget = field(default_factory=AttemptBudget)
    quality_profile: str = DEFAULT_QUALITY_PROFILE
    route_name: str = ""
    plan_version: str = DEFAULT_PLAN_VERSION
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.primary_parser_id:
            raise ValueError("ParsePlan requires a non-empty primary_parser_id")
        if self.quality_profile not in SUPPORTED_QUALITY_PROFILES:
            raise ValueError(
                f"unknown quality profile {self.quality_profile!r}; expected one of "
                f"{sorted(SUPPORTED_QUALITY_PROFILES)}"
            )
        chain = (self.primary_parser_id, *self.fallback_parser_ids)
        if len(set(chain)) != len(chain):
            raise ValueError(
                f"duplicate parser ids in backend chain: {chain}"
            )
        if len(chain) > self.budget.max_backend_attempts:
            raise ValueError(
                f"backend chain length {len(chain)} exceeds budget "
                f"max_backend_attempts={self.budget.max_backend_attempts}"
            )

    def backend_chain(self) -> tuple[str, ...]:
        """完整尝试顺序：primary 在前，fallback 按声明顺序."""
        return (self.primary_parser_id, *self.fallback_parser_ids)


__all__ = [
    "AttemptBudget",
    "DEFAULT_PLAN_VERSION",
    "DEFAULT_QUALITY_PROFILE",
    "ParsePlan",
    "SUPPORTED_QUALITY_PROFILES",
]
