"""Immutable schema contract for the 52-table convergence release."""

from __future__ import annotations


CURRENT_SCHEMA_VERSION = "2026.09.converged-v1"
CURRENT_SCHEMA_CHECKSUM = "8966fa3f01d5d2f3ead7db8091c043e55fc4d4b6ae7e3c9c3142732543f4baed"
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
    "kb_document_refs",
    "kb_purge_tasks",
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
    "serving_query_logs",
})
EXPECTED_FORMAL_TABLES = len(FORMAL_TABLES)
EXPECTED_PHYSICAL_TABLES = EXPECTED_FORMAL_TABLES + 1

# One-hop support starts at the complete release immediately preceding design 51.
# The bridge is allowed to add only these four design-51 tables; an arbitrarily
# old or partially bootstrapped database is not silently guessed forward.
PRE51_REQUIRED_TABLES: frozenset[str] = FORMAL_TABLES - BRIDGE_51_TABLES

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
)

if len(RETIRED_TABLES) != len(set(RETIRED_TABLES)):  # pragma: no cover - import guard
    raise RuntimeError("RETIRED_TABLES contains duplicates")

if EXPECTED_FORMAL_TABLES != 52:  # pragma: no cover - import guard
    raise RuntimeError("FORMAL_TABLES must contain exactly 52 names")
