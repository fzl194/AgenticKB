from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from knowledge_mining.mining.maintenance.database_upgrade.manifest import (
    AppliedMigrationMismatch,
    ManifestError,
    load_manifest,
    manifest_checksum,
    pending_migrations,
)
from knowledge_mining.mining.maintenance.database_upgrade.contract import (
    CURRENT_SCHEMA_CHECKSUM,
    LEGACY_COMPAT_TABLES,
    RETIRED_TABLES,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_manifest_loads_ordered_checksum_verified_migrations(tmp_path: Path) -> None:
    migration_dir = tmp_path / "core"
    migration_dir.mkdir()
    first = migration_dir / "001.sql"
    second = migration_dir / "002.sql"
    first.write_text("SELECT 1;\n", encoding="utf-8")
    second.write_text("SELECT 2;\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: test-v2\n"
        "migrations:\n"
        "  - id: core/001\n"
        "    path: core/001.sql\n"
        f"    checksum: {_sha(first.read_bytes())}\n"
        "  - id: core/002\n"
        "    path: core/002.sql\n"
        f"    checksum: {_sha(second.read_bytes())}\n",
        encoding="utf-8",
    )

    loaded = load_manifest(manifest)

    assert loaded.schema_version == "test-v2"
    assert [item.migration_id for item in loaded.migrations] == ["core/001", "core/002"]
    assert pending_migrations(loaded, {"core/001": _sha(first.read_bytes())}) == (
        loaded.migrations[1],
    )


def test_manifest_rejects_file_checksum_drift(tmp_path: Path) -> None:
    sql_file = tmp_path / "001.sql"
    sql_file.write_text("SELECT 1;\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: test-v1\n"
        "migrations:\n"
        "  - id: core/001\n"
        "    path: 001.sql\n"
        "    checksum: deadbeef\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="checksum"):
        load_manifest(manifest)


def test_pending_rejects_changed_already_applied_migration(tmp_path: Path) -> None:
    sql_file = tmp_path / "001.sql"
    sql_file.write_text("SELECT 1;\n", encoding="utf-8")
    checksum = _sha(sql_file.read_bytes())
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: test-v1\n"
        "migrations:\n"
        "  - id: core/001\n"
        "    path: 001.sql\n"
        f"    checksum: {checksum}\n",
        encoding="utf-8",
    )

    loaded = load_manifest(manifest)
    with pytest.raises(AppliedMigrationMismatch, match="core/001"):
        pending_migrations(loaded, {"core/001": "different"})


def test_manifest_rejects_duplicate_ids_and_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.sql"
    outside.write_text("SELECT 1;\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: test-v1\n"
        "migrations:\n"
        "  - id: core/001\n"
        "    path: ../outside.sql\n"
        f"    checksum: {_sha(outside.read_bytes())}\n"
        "  - id: core/001\n"
        "    path: ../outside.sql\n"
        f"    checksum: {_sha(outside.read_bytes())}\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError):
        load_manifest(manifest)


def test_repository_manifest_drops_every_retired_table_without_cascade() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    manifest = load_manifest(repo_root / "databases" / "migrations" / "manifest.yaml")
    assert manifest_checksum(manifest) == CURRENT_SCHEMA_CHECKSUM
    drop_migration = next(
        item for item in manifest.migrations if item.migration_id.endswith("drop_retired_tables")
    )
    sql = drop_migration.path.read_text(encoding="utf-8")

    assert "CASCADE" not in "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    for table in RETIRED_TABLES:
        assert f"DROP TABLE IF EXISTS {table} RESTRICT;" in sql
    for table in LEGACY_COMPAT_TABLES:
        assert f"DROP TABLE IF EXISTS {table} RESTRICT;" in sql
    assert "old_key.key_hash" in sql
    assert "new_open.key_id = new_key.id" in sql
