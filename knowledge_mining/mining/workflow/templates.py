"""批次8 M0（24 号 §8/§10.1）：旧 7 类挖掘模板全部退役，内置模板清空。

旧 minimal/fast_retrieval/discourse_only/entity_graph/hybrid_knowledge/
ontology_only/full 模板引用已删除算子（enrich/discourse_line/
contextual_retrieval_enrich/retrieval_unit_build 及实体/本体线），
不再提供。M6 将以 4 套新预置对应的模板重建（轻量关键词/标准混合/
问题别名增强/长文档全局增强）。在此之前新建 Workflow 必须显式给 graph。
"""
from __future__ import annotations

from .graph import WorkflowGraph


def builtin_templates() -> dict[str, WorkflowGraph]:
    return {}


def builtin_templates_v2() -> dict[str, WorkflowGraph]:
    """Compatibility import name for the only supported template generation."""
    return builtin_templates()
