"""Immutable schema contract for the 52-table convergence release."""

from __future__ import annotations


CURRENT_SCHEMA_VERSION = "2026.09.user-lifecycle-retrieval-v1"
# 1.1.14 勘误：加 006 迁移时必须同步刷新本镜像常量（= manifest_checksum）——
# 漏刷导致 llm_service 启动 fail-closed 校验仍等旧值而拒绝启动
# （guard: tests/maintenance/test_migration_manifest.py 断言两者相等）。
# 1.1.15 资产包替换：加 009（v2 向量 HNSW 索引）后刷新。
# 2026-10-20 三次替换：009 修订（串行建索引，绕开 PG 容器 64MB /dev/shm——
# 并行建索引的共享排序区按 maintenance_work_mem 申请动态共享内存会 ENOSPC）。
# 2026-10-26 整库导出：加 010（kb_export_tasks）后刷新。
# 63号批次1：加 010（wiki 制品底座四表+页面单元 HNSW）后刷新。
# 66号批次1：加 011（wiki 目录草稿表 kb_wiki_tocs）后刷新。
# 2026-11-10 合并 fzl194/master：整库导出与 wiki 两条分支各自用掉 010——
# 迁移 id 含日期+表名全局唯一（双 010 保留，ledger 按整串 id 记账），合并后刷新。
# 67号工作台 MVP：加 012（kb_workspace_* 七表）后刷新。
# 图片VLM图注方案B：加 013（asset_figure_caption_cache）后刷新。
# S1 文件格式支持补全：加 014（快照 MIME 白名单放宽，legacy office）后刷新。
CURRENT_SCHEMA_CHECKSUM = "574a943b4ab9e5dc4c6a1b60d85d559d4a6fc00d01ba92b8dd1d6e84ffacf3ca"
SCHEMA_MARKER_ID = f"schema/{CURRENT_SCHEMA_VERSION}"
MIGRATION_LEDGER_TABLE = "cmkb_schema_migrations"
LEGACY_COMPAT_TABLES: tuple[str, ...] = ("mcp_open_kbs", "mcp_access")
BRIDGE_51_TABLES: frozenset[str] = frozenset(
    {"user_domains", "mcp_keys", "mcp_key_open_kbs", "kb_purge_tasks"}
)

# The names are part of the release contract, not merely a table-count target.
# This lets plan/apply reject an unsupported or dirty source before cloning it.
FORMAL_TABLES: frozenset[str] = frozenset({
    "mining_workflows",
    "mining_workflow_versions",
    "agent_llm_prompt_templates",
    "agent_llm_tasks",
    "agent_llm_requests",
    "agent_llm_attempts",
    "agent_llm_results",
    "agent_llm_events",
    "agent_llm_model_calls",
    "mining_runs",
    "mining_run_documents",
    "mining_run_stage_events",
    "mining_workflow_node_events",
    "kb_users",
    "knowledge_bases",
    "kb_members",
    "kb_folders",
    "user_domains",
    "mcp_keys",
    "mcp_key_open_kbs",
    "mcp_key_open_artifacts",
    "kb_document_refs",
    "kb_purge_tasks",
    "kb_export_tasks",
    "asset_source_batches",
    "asset_storage_objects",
    "asset_documents",
    "asset_document_snapshots",
    "asset_document_snapshot_links",
    "asset_parse_runs",
    "asset_raw_segments",
    "asset_builds",
    "asset_build_document_snapshots",
    "asset_structure_nodes",
    "asset_structure_edges",
    "asset_structured_assets",
    "asset_table_cells",
    "asset_retrieval_units_v2",
    "asset_retrieval_embeddings_v2",
    "asset_snapshot_readiness",
    "asset_source_locators",
    "asset_structure_nodes_staging",
    "asset_structure_edges_staging",
    "asset_structured_assets_staging",
    "asset_table_cells_staging",
    "asset_retrieval_units_v2_staging",
    "asset_retrieval_embeddings_v2_staging",
    "asset_snapshot_readiness_staging",
    "asset_source_locators_staging",
    "onenet_imports",
    "onenet_toc_cache",
    "operator_paradigm",
    "operator_paradigm_version",
    "knowledge_access_records",
    "knowledge_access_record_payloads",
    "kb_artifacts",
    "kb_artifact_revisions",
    "kb_artifact_members",
    "kb_artifact_batches",
    "kb_table_columns",
    "kb_table_rows",
    # wiki 制品通用底座（63号批次1，迁移 010）：来源绑定通用表 + wiki 专有三表
    "kb_artifact_sources",
    "kb_wiki_pages",
    "kb_wiki_page_links",
    "kb_wiki_page_units",
    # wiki 目录草稿（66号批次1，迁移 011）：设计态 toc，发布时冻结进 revisions
    "kb_wiki_tocs",
    # 知识制品工作台（67号 MVP，迁移 012）：登记/成员/来源/运行/输出/绑定/导出
    "kb_workspaces",
    "kb_workspace_members",
    "kb_workspace_sources",
    "kb_workspace_runs",
    "kb_workspace_run_outputs",
    "kb_workspace_output_bindings",
    "kb_workspace_exports",
    # VLM 图片图注缓存（图片VLM方案B，迁移 013）：方案A/B 共用
    "asset_figure_caption_cache",
})
EXPECTED_FORMAL_TABLES = len(FORMAL_TABLES)
EXPECTED_PHYSICAL_TABLES = EXPECTED_FORMAL_TABLES + 1

# This release accepts the complete immediately preceding table set: the old
# Java log exists and the unified ledger does not yet exist. The kb_* artifact
# six-table set (61号), the key-open-artifacts table (008), and the wiki
# foundation four-table set (63号批次1，迁移 010：kb_artifact_sources 与 wiki 专有
# 三表), and the wiki toc draft table (66号批次1，迁移 011) are new in this
# release, so they are subtracted alongside the access-ledger pair when
# reconstructing the previous release baseline——
# 漏减会让 validate_supported_rebase_source 拒认 1.1.15 源库（一跳升级被误判）。
PREVIOUS_RELEASE_TABLES: frozenset[str] = frozenset(
    (
        FORMAL_TABLES
        - {
            "knowledge_access_records",
            "knowledge_access_record_payloads",
            "kb_artifacts",
            "kb_artifact_revisions",
            "kb_artifact_members",
            "kb_artifact_batches",
            "kb_table_columns",
            "kb_table_rows",
            "mcp_key_open_artifacts",
            "kb_export_tasks",
            "kb_artifact_sources",
            "kb_wiki_pages",
            "kb_wiki_page_links",
            "kb_wiki_page_units",
            "kb_wiki_tocs",
            # 67号工作台七表（迁移 012）本版新增，源库必然缺席，一并从上一版基线扣除
            "kb_workspaces",
            "kb_workspace_members",
            "kb_workspace_sources",
            "kb_workspace_runs",
            "kb_workspace_run_outputs",
            "kb_workspace_output_bindings",
            "kb_workspace_exports",
            # VLM 图注缓存表（迁移 013）本版新增，一并从上一版基线扣除
            "asset_figure_caption_cache",
        }
    )
    | {"serving_query_logs"}
)
# Compatibility export for older callers/tests. New cutover source validation
# uses PREVIOUS_RELEASE_TABLES.
PRE51_REQUIRED_TABLES: frozenset[str] = PREVIOUS_RELEASE_TABLES - BRIDGE_51_TABLES

RETIRED_TABLES: tuple[str, ...] = (
    "asset_upload_sessions",
    "asset_storage_object_refs",
    "asset_file_audit_events",
    "asset_storage_quotas",
    "asset_storage_operations",
    "asset_parse_run_attempts",
    "asset_raw_segment_relations",
    "asset_segment_element_links",
    "asset_retrieval_units",
    "asset_retrieval_embeddings",
    "ontology_versions",
    "ontology_node_types",
    "ontology_relation_types",
    "ontology_entities",
    "ontology_entity_relations",
    "ontology_alias_dictionary",
    "ontology_evidence_nodes",
    "ontology_candidates",
    "asset_segment_entity_mentions",
    "asset_publish_releases",
    "serving_query_cache",
    "serving_query_logs",
)

if len(RETIRED_TABLES) != len(set(RETIRED_TABLES)):  # pragma: no cover - import guard
    raise RuntimeError("RETIRED_TABLES contains duplicates")

if EXPECTED_FORMAL_TABLES != 74:  # pragma: no cover - import guard
    raise RuntimeError("FORMAL_TABLES must contain exactly 74 names")
