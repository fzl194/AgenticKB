"""
共享的数据库表定义，供 export_db.py / import_db.py / reset_db.py 使用。

导出顺序：父表先导出，导入时按此顺序（满足外键约束）。
TRUNCATE 顺序：反序（先清子表，再清父表）。
"""
from __future__ import annotations

EXPORT_TABLES = [
    # 版本化迁移账本（结构与数据必须一起恢复）
    "cmkb_schema_migrations",
    # mining_control（全局 Workflow 定义）
    "mining_workflows",
    "mining_workflow_versions",
    # agent_llm_runtime
    "agent_llm_prompt_templates",
    "agent_llm_tasks",
    "agent_llm_requests",
    "agent_llm_attempts",
    "agent_llm_results",
    "agent_llm_events",
    "agent_llm_model_calls",
    # mining_runtime
    "mining_runs",
    "mining_run_documents",
    "mining_run_stage_events",
    "mining_workflow_node_events",
    # 身份与 KB
    "kb_users",
    "knowledge_bases",
    "kb_members",
    "kb_folders",
    "user_domains",
    "mcp_keys",
    "mcp_key_open_kbs",
    "kb_document_refs",
    "kb_purge_tasks",
    # 整库导出任务（018 启动链 / core 010 迁移）
    "kb_export_tasks",
    # asset_core 核心身份、对象与版本
    "asset_source_batches",
    "asset_storage_objects",
    "asset_documents",
    "asset_document_snapshots",
    "asset_document_snapshot_links",
    "asset_parse_runs",
    "asset_raw_segments",
    "asset_builds",
    "asset_build_document_snapshots",
    # v2 正式资产
    "asset_structure_nodes",
    "asset_structure_edges",
    "asset_structured_assets",
    "asset_table_cells",
    "asset_retrieval_units_v2",
    "asset_retrieval_embeddings_v2",
    "asset_snapshot_readiness",
    "asset_source_locators",
    # v2 staging
    "asset_structure_nodes_staging",
    "asset_structure_edges_staging",
    "asset_structured_assets_staging",
    "asset_table_cells_staging",
    "asset_retrieval_units_v2_staging",
    "asset_retrieval_embeddings_v2_staging",
    "asset_snapshot_readiness_staging",
    "asset_source_locators_staging",
    # 功能面
    "onenet_imports",
    "onenet_toc_cache",
    # operator（检索范式）——见 OPTIONAL_TABLES
    "operator_paradigm",
    "operator_paradigm_version",
    # 统一 Web/API/MCP 检索与读取记录
    "knowledge_access_records",
    "knowledge_access_record_payloads",
    # 知识制品（61号表格制品 MVP）：挂 domain 的策展数据，启动链 017 必建
    "kb_artifacts",
    "kb_artifact_revisions",
    "kb_artifact_members",
    "kb_artifact_batches",
    "kb_table_columns",
    "kb_table_rows",
    # 008：钥匙级制品开放子集（1.1.15 权限模型，017 启动链同建）
    "mcp_key_open_artifacts",
    # wiki 制品通用底座（63号批次1，迁移 010，017/018 启动链同文）：
    # 来源绑定通用表 + wiki 专有三表（均无外键，物理位置不影响导入顺序）
    "kb_artifact_sources",
    "kb_wiki_pages",
    "kb_wiki_page_links",
    "kb_wiki_page_units",
    # wiki 目录草稿（66号批次1，迁移 011，019 启动链同文；无外键）
    "kb_wiki_tocs",
    # 知识制品工作台（67号 MVP，迁移 012，021 启动链同文）：父表先导
    # （workspaces→members/sources，runs→run_outputs；bindings/exports 无外键）
    "kb_workspaces",
    "kb_workspace_members",
    "kb_workspace_sources",
    "kb_workspace_runs",
    "kb_workspace_run_outputs",
    "kb_workspace_output_bindings",
    "kb_workspace_exports",
    # 图片VLM图注方案B（迁移 013）：VLM 图注缓存
    "asset_figure_caption_cache",
]

# 兼容尚未完成 52 号 bootstrap 的测试/旧库；生产收敛库验证要求这些表存在。
OPTIONAL_TABLES = {
    "operator_paradigm",
    "operator_paradigm_version",
    "knowledge_access_records",
    "knowledge_access_record_payloads",
}
