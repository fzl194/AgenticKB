from __future__ import annotations

from pathlib import Path

from knowledge_mining.mining.infra.pg_schema import primary_schema_paths
from knowledge_mining.mining.maintenance.database_upgrade.contract import (
    FORMAL_TABLES,
    RETIRED_TABLES,
)
from knowledge_mining.mining.maintenance.database_upgrade.manifest import (
    MigrationMode,
    load_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_knowledge_access_records_ddl_is_in_current_bootstrap() -> None:
    paths = primary_schema_paths()
    ddl_path = REPO_ROOT / "databases/kb/schemas/015_knowledge_access_records.sql"

    assert ddl_path in paths
    sql = ddl_path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS knowledge_access_records" in sql
    for column in (
        "actor_user_id",
        "actor_username",
        "source",
        "operation",
        "tool_name",
        "mcp_key_id",
        "kb_ids",
        "status",
        "error_code",
        "details_json",
    ):
        assert column in sql
    assert "USING GIN (kb_ids)" in sql
    assert "REFERENCES kb_users" not in sql
    assert "REFERENCES mcp_keys" not in sql


def test_retrieval_records_replace_legacy_table_in_schema_contract() -> None:
    assert "knowledge_access_records" in FORMAL_TABLES
    assert "serving_query_logs" not in FORMAL_TABLES
    assert "serving_query_logs" in RETIRED_TABLES
    assert "knowledge_access_records" not in RETIRED_TABLES


def test_repository_manifest_uses_offline_rebase_for_one_time_cutover() -> None:
    manifest = load_manifest(REPO_ROOT / "databases/migrations/manifest.yaml")
    migration = next(
        item
        for item in manifest.migrations
        if item.migration_id.endswith("replace_serving_query_logs")
    )
    sql = migration.path.read_text(encoding="utf-8")

    assert migration.mode is MigrationMode.OFFLINE_REBASE
    assert "DROP TABLE IF EXISTS serving_query_logs RESTRICT;" in sql
    assert "knowledge_access_records" in sql
    drop_lines = [
        line for line in sql.splitlines()
        if line.lstrip().upper().startswith("DROP TABLE")
    ]
    assert "CASCADE" not in "\n".join(drop_lines)
    assert not any(
        line.lstrip().upper().startswith("TRUNCATE")
        for line in sql.splitlines()
    )
    assert "DELETE FROM knowledge_access_records" not in sql



def test_payload_ddl_is_registered_and_uses_one_to_one_cascade_lifecycle() -> None:
    paths = primary_schema_paths()
    ddl_path = REPO_ROOT / "databases/kb/schemas/016_knowledge_access_record_payloads.sql"

    assert ddl_path in paths
    sql = ddl_path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS knowledge_access_record_payloads" in sql
    assert "record_id TEXT PRIMARY KEY" in sql
    assert "REFERENCES knowledge_access_records(id) ON DELETE CASCADE" in sql
    assert "response_mode IN ('snapshot', 'reference', 'summary')" in sql
    for column in (
        "request_json", "effective_context_json", "response_json",
        "response_refs_json", "request_bytes", "response_bytes",
        "response_truncated", "response_original_bytes",
        "response_omitted_count", "response_sha256", "redactions_json",
        "payload_schema_version",
    ):
        assert column in sql


def _create_table_segment(sql: str, table: str) -> str:
    marker = f"CREATE TABLE IF NOT EXISTS {table}"
    start = sql.index(marker)
    end = sql.index(";", start) + 1
    return " ".join(sql[start:end].split())


def test_formal_schema_and_cutover_migration_create_identical_ledger_tables() -> None:
    migration = (
        REPO_ROOT
        / "databases/migrations/core/20260928_005_replace_serving_query_logs.sql"
    ).read_text(encoding="utf-8")
    for number, table in (
        ("015", "knowledge_access_records"),
        ("016", "knowledge_access_record_payloads"),
    ):
        formal = (
            REPO_ROOT / f"databases/kb/schemas/{number}_{table}.sql"
        ).read_text(encoding="utf-8")
        assert _create_table_segment(formal, table) == _create_table_segment(
            migration, table
        )


