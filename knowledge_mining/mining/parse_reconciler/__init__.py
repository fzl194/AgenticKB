"""Structural Reconciler 包（SRS §C08 / §4.8，整改轮最小实现）.

文档级跨元素/跨页结构修复——规则与 parser 分离（用户指令：把跨元素、
跨页和家具规则迁到 Reconciler，不再继续膨胀 native_pdf adapter）。
"""
from knowledge_mining.mining.parse_reconciler.reconciler import (
    RECONCILER_VERSION,
    PatchRecord,
    ReconcileResult,
    StructuralReconciler,
)

__all__ = [
    "RECONCILER_VERSION",
    "PatchRecord",
    "ReconcileResult",
    "StructuralReconciler",
]
