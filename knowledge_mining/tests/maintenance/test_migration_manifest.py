from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from knowledge_mining.mining.maintenance.database_upgrade.manifest import (
    AppliedMigrationMismatch,
    ManifestError,
    Migration,
    MigrationMode,
    load_manifest,
    manifest_checksum,
    pending_migrations,
    requires_rebase,
)
from knowledge_mining.mining.maintenance.database_upgrade.contract import (
    CURRENT_SCHEMA_CHECKSUM,
    LEGACY_COMPAT_TABLES,
    RETIRED_TABLES,
)


def _sha(data: bytes) -> str:
    normalized = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
        "    mode: offline_rebase\n"
        f"    checksum: {_sha(first.read_bytes())}\n"
        "  - id: core/002\n"
        "    path: core/002.sql\n"
        "    mode: online_expand\n"
        f"    checksum: {_sha(second.read_bytes())}\n",
        encoding="utf-8",
    )

    loaded = load_manifest(manifest)

    assert loaded.schema_version == "test-v2"
    assert [item.migration_id for item in loaded.migrations] == ["core/001", "core/002"]
    assert [item.mode for item in loaded.migrations] == [
        MigrationMode.OFFLINE_REBASE,
        MigrationMode.ONLINE_EXPAND,
    ]
    assert requires_rebase(loaded.migrations) is True
    assert requires_rebase((loaded.migrations[1],)) is False
    assert pending_migrations(loaded, {"core/001": _sha(first.read_bytes())}) == (
        loaded.migrations[1],
    )


def test_non_reentrant_backfill_requires_clone_but_reentrant_can_run_online(
    tmp_path: Path,
) -> None:
    sql = tmp_path / "backfill.sql"
    sql.write_text("SELECT 1;", encoding="utf-8")
    checksum = _sha(sql.read_bytes())

    unsafe = Migration("backfill/unsafe", sql, checksum, MigrationMode.BACKFILL, False)
    safe = Migration("backfill/safe", sql, checksum, MigrationMode.BACKFILL, True)

    assert requires_rebase((unsafe,)) is True
    assert requires_rebase((safe,)) is False


def test_manifest_rejects_unknown_migration_mode(tmp_path: Path) -> None:
    sql_file = tmp_path / "001.sql"
    sql_file.write_text("SELECT 1;\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: test-v1\n"
        "migrations:\n"
        "  - id: core/001\n"
        "    path: 001.sql\n"
        "    mode: magic_copy\n"
        f"    checksum: {_sha(sql_file.read_bytes())}\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="mode"):
        load_manifest(manifest)


def test_manifest_defaults_legacy_entries_to_offline_rebase(tmp_path: Path) -> None:
    sql_file = tmp_path / "001.sql"
    sql_file.write_text("SELECT 1;\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: test-v1\n"
        "migrations:\n"
        "  - id: core/001\n"
        "    path: 001.sql\n"
        f"    checksum: {_sha(sql_file.read_bytes())}\n",
        encoding="utf-8",
    )

    assert load_manifest(manifest).migrations[0].mode is MigrationMode.OFFLINE_REBASE


def test_manifest_checksum_pins_operational_mode(tmp_path: Path) -> None:
    sql_file = tmp_path / "001.sql"
    sql_file.write_text("SELECT 1;\n", encoding="utf-8")
    checksum = _sha(sql_file.read_bytes())

    online = load_manifest(
        _write_single_migration_manifest(
            tmp_path / "online.yaml", sql_file, checksum, "online_expand"
        )
    )
    offline = load_manifest(
        _write_single_migration_manifest(
            tmp_path / "offline.yaml", sql_file, checksum, "offline_rebase"
        )
    )

    assert manifest_checksum(online) != manifest_checksum(offline)


def _write_single_migration_manifest(
    path: Path, sql_file: Path, checksum: str, mode: str
) -> Path:
    path.write_text(
        "schema_version: test-v1\n"
        "migrations:\n"
        "  - id: core/001\n"
        f"    path: {sql_file.name}\n"
        f"    mode: {mode}\n"
        f"    checksum: {checksum}\n",
        encoding="utf-8",
    )
    return path


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
    sql = "\n".join(
        item.path.read_text(encoding="utf-8")
        for item in manifest.migrations
        if item.mode is MigrationMode.OFFLINE_REBASE
    )

    drop_lines = [
        line for line in sql.splitlines()
        if line.lstrip().upper().startswith("DROP TABLE")
    ]
    assert "CASCADE" not in "\n".join(drop_lines)
    for table in RETIRED_TABLES:
        assert f"DROP TABLE IF EXISTS {table} RESTRICT;" in sql
    for table in LEGACY_COMPAT_TABLES:
        assert f"DROP TABLE IF EXISTS {table} RESTRICT;" in sql
    assert "new_key.user_id = old_key.user_id" in sql
    assert "new_key.id = new_open.key_id" in sql or "new_key.id = grant_row.key_id" in sql
    assert "kb.domain IS DISTINCT FROM new_key.domain" in sql


@pytest.mark.parametrize(
    "sql_text",
    [
        "DROP TABLE knowledge_access_records;",
        "TRUNCATE TABLE knowledge_access_record_payloads;",
        "DELETE FROM knowledge_access_records;",
        "DELETE FROM knowledge_access_record_payloads /* still no where */;",
        "WITH doomed AS (SELECT id FROM scratch WHERE id = 1) DELETE FROM knowledge_access_records USING doomed;",
        "TRUNCATE TABLE scratch, knowledge_access_record_payloads RESTART IDENTITY;",
    ],
)
def test_online_expand_rejects_destructive_access_ledger_sql(
    tmp_path: Path, sql_text: str,
) -> None:
    sql_file = tmp_path / "unsafe.sql"
    sql_file.write_text(sql_text, encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: test-v1\n"
        "migrations:\n"
        "  - id: core/unsafe\n"
        "    path: unsafe.sql\n"
        "    mode: online_expand\n"
        f"    checksum: {_sha(sql_file.read_bytes())}\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="knowledge_access"):
        load_manifest(manifest)


def test_online_expand_allows_scoped_delete_from_unrelated_table(tmp_path: Path) -> None:
    sql_file = tmp_path / "safe.sql"
    sql_file.write_text("DELETE FROM scratch WHERE id = 1;", encoding="utf-8")
    manifest = _write_single_migration_manifest(
        tmp_path / "manifest.yaml",
        sql_file,
        _sha(sql_file.read_bytes()),
        "online_expand",
    )

    assert load_manifest(manifest).migrations[0].mode is MigrationMode.ONLINE_EXPAND


def test_java_serving_checksum_mirror_matches_manifest() -> None:
    """1.1.14 勘误守卫：serving 的 EXPECTED_SCHEMA_CHECKSUM 是 manifest 校验和的
    跨语言镜像（fail-closed 启动校验）——改迁移清单必须三处同刷（manifest/
    contract.py/Java），漏刷即服务拒绝启动。本用例钉 Java 镜像；
    llm_service 走 contract.CURRENT_SCHEMA_CHECKSUM，由既有用例覆盖。"""
    import re as _re

    repo_root = Path(__file__).resolve().parents[3]
    manifest = load_manifest(repo_root / "databases" / "migrations" / "manifest.yaml")
    java_path = (
        repo_root / "agent_serving_java" / "src" / "main" / "java"
        / "com" / "coremasterkb" / "serving" / "observability"
        / "ServingRuntimeSchemaInitializer.java"
    )
    java_text = java_path.read_text(encoding="utf-8")
    match = _re.search(r'EXPECTED_SCHEMA_CHECKSUM =\s*"\s*\n?\s*"([0-9a-f]{64})"', java_text)
    if match is None:
        # 常量单行书写形态
        match = _re.search(r'EXPECTED_SCHEMA_CHECKSUM =\s*"([0-9a-f]{64})"', java_text)
    assert match is not None, "Java 侧 EXPECTED_SCHEMA_CHECKSUM 未找到"
    assert match.group(1) == manifest_checksum(manifest), (
        "Java 镜像与 manifest 校验和不一致——改迁移清单必须同步刷新 "
        "contract.CURRENT_SCHEMA_CHECKSUM 与 ServingRuntimeSchemaInitializer"
        ".EXPECTED_SCHEMA_CHECKSUM（否则服务启动 fail-closed 校验拒绝就绪）"
    )
