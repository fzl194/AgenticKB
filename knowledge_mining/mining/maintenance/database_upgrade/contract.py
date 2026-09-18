"""Immutable schema contract for the 52-table convergence release."""

from __future__ import annotations


CURRENT_SCHEMA_VERSION = "2026.09.converged-v1"
CURRENT_SCHEMA_CHECKSUM = "017512ec7938511f094d529e8e2a207b8b64c762940bcb6149aa668e978f0ae9"
SCHEMA_MARKER_ID = f"schema/{CURRENT_SCHEMA_VERSION}"
EXPECTED_FORMAL_TABLES = 52
EXPECTED_PHYSICAL_TABLES = 53
MIGRATION_LEDGER_TABLE = "cmkb_schema_migrations"
LEGACY_COMPAT_TABLES: tuple[str, ...] = ("mcp_open_kbs", "mcp_access")
MINIMUM_REBASE_TABLES: frozenset[str] = frozenset(
    {"user_domains", "mcp_keys", "mcp_key_open_kbs", "kb_purge_tasks"}
)

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
)

if len(RETIRED_TABLES) != len(set(RETIRED_TABLES)):  # pragma: no cover - import guard
    raise RuntimeError("RETIRED_TABLES contains duplicates")
