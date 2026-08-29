"""研究算子 handler 隔离区（批次8 M0，24 号文档 §5.10-§5.16）。

实体/本体/图谱七算子的 handler 代码在此保留用于研究，但两个 map 均
**不接入** `builtin_handler_registry()`——正式 Run 永远解析不到这些类型。
未来启用必须重新完成「生产—持久化—检索消费—评测」全闭环设计，
不允许仅把 map 接回去就恢复。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from knowledge_mining.mining.pipeline import (
    entity_extract_stage,
    entity_relations_stage,
    resolve_stage,
)

from ..core import OperatorResult
from ..operators.options import (
    EntityExtractOptions,
    EntityRelationOptions,
    EntityResolveOptions,
)
from .document import _document_handler


def entity_extract_handler(state, params, runtime) -> OperatorResult:
    return _document_handler(
        state,
        params,
        runtime,
        options_type=EntityExtractOptions,
        stage=entity_extract_stage,
        capability="entity_mentions",
        error_status=3,  # OperatorStatus.SKIPPED（避免研究区依赖正式枚举演进）
        error_code="entity_extract_empty_fallback",
        ontology_required=True,
    )


def entity_resolve_handler(state, params, runtime) -> OperatorResult:
    return _document_handler(
        state,
        params,
        runtime,
        options_type=EntityResolveOptions,
        stage=resolve_stage,
        capability="resolved_entities",
        error_status=3,
        error_code="entity_resolve_empty_fallback",
        ontology_required=True,
    )


def entity_relation_extract_handler(state, params, runtime) -> OperatorResult:
    return _document_handler(
        state,
        params,
        runtime,
        options_type=EntityRelationOptions,
        stage=entity_relations_stage,
        capability="entity_relations",
        error_status=3,
        error_code="entity_relation_empty_fallback",
        ontology_required=True,
    )


from .global_nodes import (  # noqa: E402  研究区集中导出
    entity_review_gate_handler,
    graph_write_handler,
    ontology_induction_handler,
    ontology_review_gate_handler,
)

RESEARCH_DOCUMENT_HANDLERS: Mapping[str, Any] = {
    "entity_extract": entity_extract_handler,
    "entity_resolve": entity_resolve_handler,
    "entity_relation_extract": entity_relation_extract_handler,
}

RESEARCH_GLOBAL_HANDLERS: Mapping[str, Any] = {
    "entity_review_gate": entity_review_gate_handler,
    "ontology_induction": ontology_induction_handler,
    "ontology_review_gate": ontology_review_gate_handler,
    "graph_write": graph_write_handler,
}
