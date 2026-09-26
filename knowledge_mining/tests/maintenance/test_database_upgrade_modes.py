from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.maintenance.database_upgrade import __main__ as upgrade_main
from knowledge_mining.mining.maintenance.database_upgrade import database as database_module
from knowledge_mining.mining.maintenance.database_upgrade.database import (
    DatabaseEndpoint,
    DatabaseUpgradeError,
    clone_database,
)
from knowledge_mining.mining.maintenance.database_upgrade.manifest import (
    Migration,
    MigrationManifest,
    MigrationMode,
)


class _Rows:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _MaintenanceConnection:
    def __init__(self, *, target_exists: bool):
        self.target_exists = target_exists
        self.stat_activity_calls = 0
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        text = str(query)
        self.statements.append(text)
        if "pg_roles" in text:
            return _Rows((True,))
        if "pg_stat_activity" in text:
            self.stat_activity_calls += 1
            return _Rows((0,))
        if "pg_database" in text:
            return _Rows((1,) if self.target_exists else None)
        return _Rows(None)


def _endpoint(dbname: str = "agentickb") -> DatabaseEndpoint:
    return DatabaseEndpoint("db", 5432, dbname, "app", "secret")


def test_clone_refuses_existing_target_without_explicit_replace(monkeypatch) -> None:
    connection = _MaintenanceConnection(target_exists=True)
    monkeypatch.setattr(database_module.psycopg, "connect", lambda *_a, **_k: connection)

    with pytest.raises(DatabaseUpgradeError, match="--replace-target"):
        clone_database(_endpoint(), "agentickb_converged")

    assert not any("DROP DATABASE" in statement for statement in connection.statements)


def test_clone_drops_existing_idle_target_only_with_explicit_replace(monkeypatch) -> None:
    connection = _MaintenanceConnection(target_exists=True)
    monkeypatch.setattr(database_module.psycopg, "connect", lambda *_a, **_k: connection)

    target = clone_database(
        _endpoint(), "agentickb_converged", replace_existing=True
    )

    rendered = "\n".join(connection.statements)
    assert target.dbname == "agentickb_converged"
    assert "DROP DATABASE" in rendered
    assert "CREATE DATABASE" in rendered
    assert rendered.index("DROP DATABASE") < rendered.index("CREATE DATABASE")


def test_replace_target_cannot_delete_an_arbitrary_explicit_database(monkeypatch) -> None:
    monkeypatch.setattr(upgrade_main, "migration_admin_endpoint", lambda source: source)
    monkeypatch.setattr(
        upgrade_main,
        "clone_database",
        lambda *_args, **_kwargs: pytest.fail("unrelated target must be rejected first"),
    )

    with pytest.raises(DatabaseUpgradeError, match="固定轮换目标"):
        upgrade_main._target_endpoint(
            _endpoint(), "unrelated_database", replace_target=True
        )


def test_online_expand_applies_in_place_without_clone(monkeypatch, capsys) -> None:
    source = _endpoint()
    manifest = MigrationManifest(
        "test-v2",
        (
            Migration(
                "core/add-role",
                Path("unused.sql"),
                "checksum",
                MigrationMode.ONLINE_EXPAND,
            ),
        ),
    )
    connection = object()
    applied_connections: list[object] = []
    applied_details: list[dict[str, object]] = []

    class _ConnectionContext:
        def __enter__(self):
            return connection

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        upgrade_main,
        "_paths",
        lambda _args: (Path("."), Path("."), Path("."), Path(".")),
    )
    monkeypatch.setattr(upgrade_main, "load_default_endpoint", lambda _path: source)
    monkeypatch.setattr(upgrade_main, "load_manifest", lambda _path: manifest)
    monkeypatch.setattr(upgrade_main, "_release_version", lambda _path: "test")
    monkeypatch.setattr(upgrade_main, "load_bridge_51_policy", lambda _path: object())
    monkeypatch.setattr(upgrade_main, "_configured_database_exists", lambda _ep: True)
    monkeypatch.setattr(upgrade_main.psycopg, "connect", lambda *_a, **_k: _ConnectionContext())
    monkeypatch.setattr(upgrade_main, "ledger_exists", lambda _connection: True)
    monkeypatch.setattr(upgrade_main, "load_applied", lambda _connection: {})
    monkeypatch.setattr(
        upgrade_main,
        "apply_manifest",
        lambda current, *_args, **_kwargs: (
            applied_connections.append(current),
            applied_details.append(_kwargs["details"]),
            SimpleNamespace(applied_ids=("core/add-role",), schema_version="test-v2"),
        )[2],
    )
    monkeypatch.setattr(
        upgrade_main,
        "validate_schema",
        lambda *_args, **_kwargs: SimpleNamespace(schema_version="test-v2"),
    )
    monkeypatch.setattr(
        upgrade_main,
        "clone_database",
        lambda *_args, **_kwargs: pytest.fail("online_expand must not clone"),
    )

    assert upgrade_main._apply(SimpleNamespace(deployment_id="deploy-current")) == 0
    assert applied_connections == [connection]
    assert applied_details == [
        {"mode": "online_migrate", "deployment_id": "deploy-current"}
    ]
    assert '"mode": "online_migrate"' in capsys.readouterr().out


def test_repository_domain_role_migration_is_online_expand() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    manifest = upgrade_main.load_manifest(
        repo_root / "databases/migrations/manifest.yaml"
    )
    migration = next(
        item
        for item in manifest.migrations
        if item.migration_id.endswith("add_user_domain_role")
    )
    sql_text = migration.path.read_text(encoding="utf-8")

    assert migration.mode is MigrationMode.ONLINE_EXPAND
    assert "ADD COLUMN IF NOT EXISTS domain_role" in sql_text
    assert "DEFAULT 'member'" in sql_text


def test_repository_hard_domain_boundary_validation_blocks_orphaned_kb_access() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    manifest = upgrade_main.load_manifest(
        repo_root / "databases/migrations/manifest.yaml"
    )
    migration = next(
        item
        for item in manifest.migrations
        if item.migration_id.endswith("validate_kb_domain_memberships")
    )
    sql_text = " ".join(migration.path.read_text(encoding="utf-8").split()).lower()

    assert migration.mode is MigrationMode.ONLINE_EXPAND
    assert "raise exception" in sql_text
    assert "knowledge_bases" in sql_text
    assert "kb_members" in sql_text
    assert "user_domains" in sql_text
    assert "kb.status = 'active'" in sql_text
    assert "u.status = 'active'" in sql_text
    assert "u.site_role <> 'admin'" in sql_text


def test_rollback_config_is_noop_when_rebase_never_created_backup(
    monkeypatch, tmp_path, capsys
) -> None:
    manifest = MigrationManifest("test-v2", ())
    monkeypatch.setattr(
        upgrade_main,
        "_paths",
        lambda _args: (tmp_path, tmp_path / "config", tmp_path / "manifest", tmp_path / "backups"),
    )
    monkeypatch.setattr(upgrade_main, "load_manifest", lambda _path: manifest)

    assert upgrade_main._rollback_config(SimpleNamespace()) == 0
    assert '"mode": "rollback_config_noop"' in capsys.readouterr().out
