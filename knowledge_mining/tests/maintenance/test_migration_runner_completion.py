from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from knowledge_mining.mining.maintenance.database_upgrade.contract import SCHEMA_MARKER_ID
from knowledge_mining.mining.maintenance.database_upgrade.manifest import load_manifest
from knowledge_mining.mining.maintenance.database_upgrade.runner import apply_manifest


class _Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _Transaction:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FailSecondMigrationConnection:
    def __init__(self):
        self.sql_files_seen = 0
        self.calls: list[tuple[str, object]] = []

    def transaction(self):
        return _Transaction()

    def execute(self, query, params=None):
        text = str(query)
        self.calls.append((text, params))
        if "pg_try_advisory_lock" in text:
            return _Cursor([(True,)])
        if "pg_advisory_unlock" in text:
            return _Cursor([(True,)])
        if "to_regclass" in text:
            return _Cursor([(True,)])
        if "migration_id, checksum" in text:
            return _Cursor([])
        if text.startswith("SELECT migration_"):
            self.sql_files_seen += 1
            if self.sql_files_seen == 2:
                raise RuntimeError("second migration failed")
            return _Cursor()
        return _Cursor()


def _manifest(tmp_path: Path):
    rows = []
    for index in (1, 2):
        sql = tmp_path / f"{index:03}.sql"
        sql.write_text(f"SELECT migration_{index};\n", encoding="utf-8")
        rows.append(
            f"  - id: core/{index:03}\n"
            f"    path: {index:03}.sql\n"
            f"    checksum: {hashlib.sha256(sql.read_bytes()).hexdigest()}\n"
        )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: test-v1\nmigrations:\n" + "".join(rows),
        encoding="utf-8",
    )
    return load_manifest(manifest)


def test_partial_migration_never_writes_terminal_schema_marker(tmp_path: Path) -> None:
    connection = _FailSecondMigrationConnection()

    with pytest.raises(RuntimeError, match="second migration failed"):
        apply_manifest(
            connection,
            _manifest(tmp_path),
            app_version="1.0.0",
            validate_before_complete=lambda: None,
        )

    assert not any(
        isinstance(params, tuple) and SCHEMA_MARKER_ID in params
        for _, params in connection.calls
    )
