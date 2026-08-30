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


def test_official_presets_enable_table_rows_and_sections() -> None:
    """27号审查修复：表格行拆分（tableView=both）是标准资产契约的一部分，
    四套预置显式开启；标准混合家族（hybrid）追加章节表示。"""
    from knowledge_mining.mining.workflow.templates import builtin_templates

    templates = builtin_templates()
    for key, graph in templates.items():
        compile_node = next(
            n for n in graph.nodes if n.operator_type == "segment_compile"
        )
        assert compile_node.params.get("tableView") == "both", key

    hybrid = templates["hybrid_assets"]
    project_node = next(
        n for n in hybrid.nodes if n.operator_type == "retrieval_unit_project"
    )
    assert project_node.params.get("includeSections") is True

    lexical = templates["lexical_assets"]
    lexical_project = next(
        n for n in lexical.nodes if n.operator_type == "retrieval_unit_project"
    )
    assert not lexical_project.params.get("includeSections")



@pytest.mark.asyncio
async def test_system_preset_drift_triggers_republish():
    """27号审查修复：官方预置模板漂移（tableView/includeSections 参数更新）时，
    已发布的系统 workflow 草稿对齐并发布新版本——否则生产库旧图永不生效。"""
    from knowledge_mining.mining.workflow.presets import MINING_PRESETS
    from knowledge_mining.mining.workflow.service import WorkflowService
    from knowledge_mining.mining.workflow.templates import builtin_templates

    presets_by_key = {p.template_key: p for p in MINING_PRESETS}
    preset = presets_by_key["hybrid_assets"]

    stale_graph = builtin_templates()["lexical_assets"].to_dict()  # 假装旧模板

    class _Repo:
        def __init__(self) -> None:
            self.row = {
                "id": preset.workflow_id,
                "status": "active",
                "current_version": 1,
                "draft_revision": 3,
                "draft_graph_json": dict(stale_graph),
            }
            self.saved = None
            self.published = None

        async def get_workflow(self, workflow_id):
            # 其余预置返回"已对齐"行（ensure_system_workflows 遍历全部 4 套）
            if workflow_id == preset.workflow_id:
                return dict(self.row)
            other = next(
                (q for q in MINING_PRESETS if q.workflow_id == workflow_id),
                None,
            )
            return {
                "id": workflow_id, "status": "active", "current_version": 1,
                "draft_revision": 2,
                "draft_graph_json": builtin_templates()[
                    other.template_key
                ].to_dict(),
            } if other else None

        async def update_draft(self, workflow_id, *, graph, expected_revision,
                               updated_by):
            assert expected_revision == 3
            self.saved = graph
            self.row["draft_revision"] = 4
            self.row["draft_graph_json"] = graph
            return dict(self.row)

        async def insert_version_and_advance(self, workflow_id, *,
                                             expected_revision, version_record):
            assert expected_revision == 4
            self.published = version_record
            self.row["current_version"] = version_record["version"]
            return dict(self.row)

    class _Compiler:
        def compile(self, graph, mode="publish"):
            from types import SimpleNamespace

            plan = SimpleNamespace(
                to_manifest=lambda **kw: {
                    "graphHash": "h", "schemaVersion": "2.0",
                    "catalogVersion": "1", **kw,
                },
                graph=graph,
            )

            class _Require:
                def require_plan(self_inner):
                    return plan

            return _Require()

    service = WorkflowService.__new__(WorkflowService)
    service.repository = _Repo()
    service.compiler = _Compiler()

    await service.ensure_workflow_library()

    repo = service.repository
    desired = builtin_templates()["hybrid_assets"].to_dict()
    assert repo.saved == desired  # 草稿对齐新模板（含 tableView=both）
    assert repo.published is not None and repo.published["version"] == 2



@pytest.mark.asyncio
async def test_system_preset_no_drift_no_touch():
    """模板未漂移（草稿 == 当前模板）时不重发布。"""
    from knowledge_mining.mining.workflow.presets import MINING_PRESETS
    from knowledge_mining.mining.workflow.service import WorkflowService
    from knowledge_mining.mining.workflow.templates import builtin_templates

    presets_by_key = {p.template_key: p for p in MINING_PRESETS}
    calls = {"publish": 0}

    class _Repo:
        async def get_workflow(self, workflow_id):
            preset = next(
                p for p in MINING_PRESETS if p.workflow_id == workflow_id
            )
            return {
                "id": workflow_id, "status": "active", "current_version": 1,
                "draft_revision": 2,
                "draft_graph_json": builtin_templates()[
                    preset.template_key
                ].to_dict(),
            }

        async def update_draft(self, *a, **k):
            raise AssertionError("no drift must not touch draft")

        async def insert_version_and_advance(self, *a, **k):
            calls["publish"] += 1
            raise AssertionError("no drift must not publish")

    service = WorkflowService.__new__(WorkflowService)
    service.repository = _Repo()
    service.compiler = None
    await service.ensure_workflow_library()
    assert calls["publish"] == 0
