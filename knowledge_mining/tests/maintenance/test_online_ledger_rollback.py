from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_mining.mining.maintenance.database_upgrade.ledger import (
    LedgerRollbackError,
    rollback_online_migration_records,
)
from knowledge_mining.mining.maintenance.database_upgrade.manifest import (
    Migration,
    MigrationManifest,
    MigrationMode,
    manifest_checksum,
)


class _Rows:
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


class _LedgerConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls: list[tuple[str, object]] = []

    def transaction(self):
        return _Transaction()

    def execute(self, query, params=None):
        text = str(query)
        self.calls.append((text, params))
        if "to_regclass" in text:
            return _Rows([(True,)])
        if text.lstrip().startswith("SELECT migration_id, checksum, app_version"):
            return _Rows(self.rows)
        return _Rows()


def _manifest() -> MigrationManifest:
    return MigrationManifest(
        "test-v2",
        (
            Migration(
                "core/old-offline",
                Path("old.sql"),
                "old-checksum",
                MigrationMode.OFFLINE_REBASE,
            ),
            Migration(
                "core/add-role",
                Path("role.sql"),
                "role-checksum",
                MigrationMode.ONLINE_EXPAND,
            ),
        ),
    )


def test_online_rollback_deletes_only_current_release_ledger_rows_and_marker() -> None:
    manifest = _manifest()
    connection = _LedgerConnection(
        [
            (
                "core/add-role",
                "role-checksum",
                "1.1.11",
                {
                    "mode": "online_migrate",
                    "migration_mode": "online_expand",
                    "deployment_id": "deploy-current",
                },
            ),
            (
                "schema/test-v2",
                manifest_checksum(manifest),
                "1.1.11",
                {
                    "mode": "online_migrate",
                    "schema_version": "test-v2",
                    "deployment_id": "deploy-current",
                },
            ),
            (
                "core/add-role",
                "role-checksum",
                "1.1.10",
                {
                    "mode": "online_migrate",
                    "migration_mode": "online_expand",
                    "deployment_id": "deploy-history",
                },
            ),
        ]
    )

    deleted = rollback_online_migration_records(
        connection,
        manifest,
        app_version="1.1.11",
        deployment_id="deploy-current",
        schema_marker_id="schema/test-v2",
    )

    assert deleted == ("core/add-role", "schema/test-v2")
    delete_call = next(call for call in connection.calls if call[0].startswith("DELETE"))
    assert set(delete_call[1][0]) == {"core/add-role", "schema/test-v2"}
    assert not any(
        keyword in query.upper()
        for query, _ in connection.calls
        for keyword in ("ALTER TABLE", "DROP TABLE", "DROP COLUMN")
    )


def test_online_rollback_ignores_historical_online_rows() -> None:
    manifest = _manifest()
    connection = _LedgerConnection(
        [
            (
                "core/add-role",
                "role-checksum",
                "1.1.10",
                {
                    "mode": "online_migrate",
                    "migration_mode": "online_expand",
                    "deployment_id": "deploy-history",
                },
            )
        ]
    )

    deleted = rollback_online_migration_records(
        connection,
        manifest,
        app_version="1.1.11",
        deployment_id="deploy-current",
        schema_marker_id="schema/test-v2",
    )

    assert deleted == ()
    assert not any(query.startswith("DELETE") for query, _ in connection.calls)


def test_backfill_is_not_auto_rolled_back_without_explicit_reentrant_contract() -> None:
    manifest = MigrationManifest(
        "test-v2",
        (
            Migration(
                "core/backfill",
                Path("backfill.sql"),
                "backfill-checksum",
                MigrationMode.BACKFILL,
            ),
        ),
    )
    connection = _LedgerConnection(
        [
            (
                "core/backfill",
                "backfill-checksum",
                "1.1.11",
                {
                    "mode": "online_migrate",
                    "migration_mode": "backfill",
                    "deployment_id": "deploy-current",
                },
            )
        ]
    )

    with pytest.raises(LedgerRollbackError, match="backfill.*不可自动回滚"):
        rollback_online_migration_records(
            connection,
            manifest,
            app_version="1.1.11",
            deployment_id="deploy-current",
            schema_marker_id="schema/test-v2",
        )

    assert not any(query.startswith("DELETE") for query, _ in connection.calls)


def test_explicitly_reentrant_backfill_can_rollback_its_ledger_record() -> None:
    manifest = MigrationManifest(
        "test-v2",
        (
            Migration(
                "core/backfill",
                Path("backfill.sql"),
                "backfill-checksum",
                MigrationMode.BACKFILL,
                reentrant=True,
            ),
        ),
    )
    connection = _LedgerConnection(
        [
            (
                "core/backfill",
                "backfill-checksum",
                "1.1.11",
                {
                    "mode": "online_migrate",
                    "migration_mode": "backfill",
                    "deployment_id": "deploy-current",
                },
            )
        ]
    )

    assert rollback_online_migration_records(
        connection,
        manifest,
        app_version="1.1.11",
        deployment_id="deploy-current",
        schema_marker_id="schema/test-v2",
    ) == ("core/backfill",)
