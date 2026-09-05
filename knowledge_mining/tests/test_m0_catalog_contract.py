"""M0 目录收口契约（批次8，24 号文档 §11 M0）。

正式挖掘算子目录只允许：
    input_ingest / document_parse / segment_compile / embedding / asset_persist / mining_finalize

旧检索资产算子（enrich / discourse_line / contextual_retrieval_enrich /
retrieval_unit_build）已删除；实体/本体七算子进入研究隔离（代码保留、正式面不可达）。
旧挖掘预置（system-full-baseline + 六条普通预置）不再 seed，M6 由 4 套新预置取代。
"""
from __future__ import annotations

from knowledge_mining.mining.workflow.handlers.document import DOCUMENT_HANDLERS
from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog

FORMAL_OPERATOR_TYPES = {
    "input_ingest",
    "document_parse",
    "segment_compile",
    "retrieval_unit_project",  # M2 加入
    "query_expansion_generate",  # M3 加入（实验可选）
    "hierarchical_summary_generate",  # M3 加入（实验可选）
    "embedding",
    "asset_persist",
    "mining_finalize",
}

RETIRED_OPERATOR_TYPES = {
    "enrich",
    "discourse_line",
    "contextual_retrieval_enrich",
    "retrieval_unit_build",
}

RESEARCH_OPERATOR_TYPES = {
    "entity_extract",
    "entity_resolve",
    "entity_relation_extract",
    "entity_review_gate",
    "ontology_induction",
    "ontology_review_gate",
    "graph_write",
}

ALLOWED_DOCUMENT_HANDLER_TYPES = {
    "parse_segment",
    "document_parse",
    "segment_compile",
    "retrieval_unit_project",
    "query_expansion_generate",
    "hierarchical_summary_generate",
    "embedding",
}


def test_formal_catalog_contains_only_allowed_operators() -> None:
    assert set(builtin_catalog()) == FORMAL_OPERATOR_TYPES


def test_retired_and_research_operators_not_registered() -> None:
    for operator_type in RETIRED_OPERATOR_TYPES | RESEARCH_OPERATOR_TYPES:
        assert operator_type not in builtin_catalog()
        assert operator_type not in DOCUMENT_HANDLERS


def test_document_handlers_only_cover_allowed_operators() -> None:
    assert set(DOCUMENT_HANDLERS) == ALLOWED_DOCUMENT_HANDLER_TYPES


def test_research_operators_isolated_in_research_module() -> None:
    from knowledge_mining.mining.workflow.operators import research

    assert set(research.RESEARCH_OPERATOR_TYPES) == RESEARCH_OPERATOR_TYPES
