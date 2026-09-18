from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from knowledge_mining.mining.maintenance.database_upgrade.contract import (
    BRIDGE_51_TABLES,
    FORMAL_TABLES,
    MIGRATION_LEDGER_TABLE,
    PRE51_REQUIRED_TABLES,
)
from knowledge_mining.mining.maintenance.database_upgrade.database import (
    DatabaseUpgradeError,
    validate_database_name,
)
from knowledge_mining.mining.maintenance.database_upgrade.manifest import load_manifest
from knowledge_mining.mining.maintenance.database_upgrade.validation import (
    SchemaValidationError,
    expected_rebase_target_tables,
    validate_schema,
    validate_supported_rebase_source,
)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _ValidationConnection:
    def __init__(self, *, tables: list[str], extensions: list[str], migration):
        self.tables = tables
        self.extensions = extensions
        self.migration = migration

    def execute(self, query, params=None):
        text = str(query)
        if "to_regclass" in text:
            return _Cursor([(True,)])
        if "migration_id = %s AND checksum = %s" in text:
            return _Cursor([(True,)])
        if "migration_id, checksum" in text:
            return _Cursor([(self.migration.migration_id, self.migration.checksum)])
        if "pg_catalog.pg_tables" in text:
            return _Cursor([(name,) for name in self.tables])
        if "pg_extension" in text:
            return _Cursor([(name,) for name in self.extensions])
        if "pg_indexes" in text:
            return _Cursor([
                ("idx_user_domains_domain", "CREATE INDEX idx_user_domains_domain ON user_domains (domain)"),
                ("idx_mcp_keys_user", "CREATE INDEX idx_mcp_keys_user ON mcp_keys (user_id)"),
                ("idx_mcp_keys_hash", "CREATE UNIQUE INDEX idx_mcp_keys_hash ON mcp_keys (key_hash)"),
                ("idx_mcp_keys_active_name", "CREATE UNIQUE INDEX idx_mcp_keys_active_name ON mcp_keys (user_id, domain, name) WHERE status = 'active'"),
            ])
        raise AssertionError(f"unexpected SQL: {text}")


def _manifest(tmp_path: Path):
    sql = tmp_path / "001.sql"
    sql.write_text("SELECT 1;\n", encoding="utf-8")
    checksum = hashlib.sha256(sql.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: test-v1\nmigrations:\n"
        "  - id: core/001\n"
        "    path: 001.sql\n"
        f"    checksum: {checksum}\n",
        encoding="utf-8",
    )
    return load_manifest(manifest)


@pytest.mark.parametrize("name", ["new_db", "_cmkb_202609", "Db123"])
def test_database_name_accepts_safe_postgresql_identifiers(name: str) -> None:
    assert validate_database_name(name) == name


@pytest.mark.parametrize("name", ["", "bad-name", "x;DROP DATABASE y", "9starts_wrong"])
def test_database_name_rejects_unsafe_values(name: str) -> None:
    with pytest.raises(DatabaseUpgradeError):
        validate_database_name(name)


def test_schema_validation_accepts_applied_manifest_and_required_extensions(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    connection = _ValidationConnection(
        tables=[MIGRATION_LEDGER_TABLE, "knowledge_bases"],
        extensions=["pg_trgm", "vector"],
        migration=manifest.migrations[0],
    )

    report = validate_schema(connection, manifest, enforce_exact_count=False)

    assert report.schema_version == "test-v1"
    assert report.extensions == ("pg_trgm", "vector")


def test_schema_validation_rejects_retired_table(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    connection = _ValidationConnection(
        tables=[MIGRATION_LEDGER_TABLE, "asset_publish_releases"],
        extensions=["pg_trgm", "vector"],
        migration=manifest.migrations[0],
    )

    with pytest.raises(SchemaValidationError, match="退役表仍存在"):
        validate_schema(connection, manifest, enforce_exact_count=False)


def test_rebase_source_requires_the_complete_supported_pre51_schema(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    connection = _ValidationConnection(
        tables=["kb_users", "knowledge_bases", "kb_members", "asset_documents"],
        extensions=["pg_trgm", "vector"],
        migration=manifest.migrations[0],
    )

    with pytest.raises(SchemaValidationError, match="不属于受支持的 pre-51 基线"):
        validate_supported_rebase_source(connection)


def test_rebase_source_accepts_complete_pre51_schema_without_51_tables(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    tables = sorted(PRE51_REQUIRED_TABLES)
    connection = _ValidationConnection(
        tables=tables,
        extensions=["pg_trgm", "vector"],
        migration=manifest.migrations[0],
    )

    assert validate_supported_rebase_source(connection) == tuple(tables)


def test_rebase_source_rejects_unknown_historical_table_before_clone(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    tables = sorted(PRE51_REQUIRED_TABLES | {"unclaimed_historical_table"})
    connection = _ValidationConnection(
        tables=tables,
        extensions=["pg_trgm", "vector"],
        migration=manifest.migrations[0],
    )

    with pytest.raises(SchemaValidationError, match="未知表"):
        validate_supported_rebase_source(connection)


def test_pre51_contract_is_exactly_formal_tables_without_bridge_tables() -> None:
    assert PRE51_REQUIRED_TABLES == FORMAL_TABLES - BRIDGE_51_TABLES


def test_pre51_rebase_target_adds_51_tables_without_requiring_them_in_source() -> None:
    source = {
        "kb_users",
        "knowledge_bases",
        "kb_members",
        "asset_documents",
        "asset_builds",
        "mcp_access",
        "mcp_open_kbs",
    }

    target = expected_rebase_target_tables(source)

    assert {"user_domains", "mcp_keys", "mcp_key_open_kbs", "kb_purge_tasks"} <= target
    assert "mcp_access" not in target
    assert "mcp_open_kbs" not in target
