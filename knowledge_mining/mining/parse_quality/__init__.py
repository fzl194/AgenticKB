"""Parse Quality Gate 包（SRS §C09 / §4.9，整改轮最小实现）.

解析成功 != 可发布：六类指标 + PASS/WARN/FAIL 决策。指标口径由
跨格式统一（禁止只报元素数量作为质量结论——用户整改指令）。
"""
from knowledge_mining.mining.parse_quality.metrics import (
    GoldenExpectations,
    QualityMetrics,
    compute_metrics,
)
from knowledge_mining.mining.parse_quality.gate import (
    QualityDecision,
    QualityGate,
    QualityProfile,
)

__all__ = [
    "GoldenExpectations",
    "QualityDecision",
    "QualityGate",
    "QualityMetrics",
    "QualityProfile",
    "compute_metrics",
]
