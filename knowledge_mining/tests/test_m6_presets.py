"""M6 契约（批次8，24 号 §8）：4 套挖掘预置 + 建库默认.

- 轻量关键词资产（无 embedding）；
- 标准混合资产（**官方默认**）；
- 问题别名增强资产（标准链 + query_expansion，embedding 前）；
- 长文档全局增强资产（标准链 + hierarchical_summary，embedding 前）；
- 旧 7 类模板/预置不再出现；resolve 默认指向 system-hybrid-assets。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.workflow.service import WorkflowService
from knowledge_mining.tests.test_mining_workflow_service import (
    MemoryWorkflowRepository,
)


EXPECTED_PRESET_KEYS = {
    "lexical_assets": {"input_ingest", "document_parse", "segment_compile",
                       "retrieval_unit_project", "asset_persist", "mining_finalize"},
    "hybrid_assets": {"input_ingest", "document_parse", "segment_compile",
                      "retrieval_unit_project", "embedding", "asset_persist",
                      "mining_finalize"},
    "query_alias_assets": {"input_ingest", "document_parse", "segment_compile",
                           "retrieval_unit_project", "query_expansion_generate",
                           "embedding", "asset_persist", "mining_finalize"},
    "longdoc_assets": {"input_ingest", "document_parse", "segment_compile",
                       "retrieval_unit_project", "hierarchical_summary_generate",
                       "embedding", "asset_persist", "mining_finalize"},
}


def test_four_preset_templates_compile_and_match_operator_sets() -> None:
    from knowledge_mining.mining.workflow.compiler import WorkflowCompiler
    from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog
    from knowledge_mining.mining.workflow.templates import builtin_templates

    templates = builtin_templates()
    assert set(templates) == set(EXPECTED_PRESET_KEYS)
    compiler = WorkflowCompiler(builtin_catalog())
    for name, graph in templates.items():
        result = compiler.compile(graph, mode="publish")
        assert result.valid is True, (name, [e.kind for e in result.errors])
        types = {node.operator_type for node in graph.nodes}
        assert types == EXPECTED_PRESET_KEYS[name], name


def test_hybrid_assets_is_the_official_default_preset() -> None:
    from knowledge_mining.mining.workflow.service import DEFAULT_WORKFLOW_ID

    assert DEFAULT_WORKFLOW_ID == "system-hybrid-assets"


@pytest.mark.asyncio
async def test_ensure_workflow_library_seeds_four_system_presets_idempotently():
    repository = MemoryWorkflowRepository()
    service = WorkflowService(repository)

    await service.ensure_workflow_library()
    await service.ensure_workflow_library()

    items = await service.list(include_archived=True)
    seeded = {item["id"] for item in items}
    assert seeded == {
        "system-lexical-assets",
        "system-hybrid-assets",
        "system-query-alias-assets",
        "system-longdoc-assets",
    }
    hybrid = next(
        item for item in items if item["id"] == "system-hybrid-assets"
    )
    assert hybrid["is_system_default"] is True
    assert hybrid["current_version"] == 1
    lexical = next(
        item for item in items if item["id"] == "system-lexical-assets"
    )
    assert lexical["is_system_default"] is False
    # 每条预置有已发布版本
    for item in items:
        assert item["current_version"] == 1


def test_legacy_preset_names_never_return() -> None:
    from knowledge_mining.mining.workflow.templates import builtin_templates

    joined = "|".join(builtin_templates())
    for legacy in ("minimal", "fast_retrieval", "discourse_only",
                   "entity_graph", "hybrid_knowledge", "ontology_only", "full"):
        assert legacy not in joined
