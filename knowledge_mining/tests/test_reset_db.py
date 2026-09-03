from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import reset_db


def test_managed_table_names_covers_known_residual_shadow_tables() -> None:
    """历史残表必须进白名单：否则真库上 reset 连 dry-run 都会被 unknown-table 拒绝。"""
    assert reset_db._is_managed_table("asset_raw_segments_staging")
    assert not reset_db._is_managed_table("another_product_orders")


def test_load_database_config_uses_control_plane_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "database.yaml"
    config_path.write_text(
        """
driver: postgresql
default:
  host: pg.internal
  port: 5433
  dbname: knowledge
  user: app
  password: secret
  sslmode: require
  gssencmode: disable
  pool_min: 2
""".strip(),
        encoding="utf-8",
    )

    config = reset_db.load_database_config(config_path)

    assert config.host == "pg.internal"
    assert config.port == 5433
    assert config.dbname == "knowledge"
    assert config.user == "app"
    assert config.password == "secret"
    assert config.sslmode == "require"
    assert "secret" not in repr(config)


def test_load_database_config_rejects_missing_required_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "database.yaml"
    config_path.write_text("default:\n  host: pg.internal\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dbname, user"):
        reset_db.load_database_config(config_path)


def test_load_database_targets_includes_domains_and_deduplicates_physical_database(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "database.yaml"
    database_path.write_text(
        """
driver: postgresql
default: {host: pg-a, port: 5432, dbname: main, user: app, password: one}
""".strip(),
        encoding="utf-8",
    )
    registry_path = tmp_path / "domain_registry.yaml"
    registry_path.write_text(
        """
domains:
  same:
    database: {host: pg-a, port: 5432, dbname: main, user: other, password: two}
  separate:
    database: {host: pg-b, port: 5433, dbname: domain_b, user: app, password: three}
""".strip(),
        encoding="utf-8",
    )

    targets = reset_db.load_database_targets(database_path, registry_path)

    assert [target.target_id for target in targets] == [
        "pg-a:5432/main",
        "pg-b:5433/domain_b",
    ]
    assert targets[0].user == "app"


def test_load_database_targets_fails_closed_without_domain_registry(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "database.yaml"
    database_path.write_text(
        "default: {host: pg-a, dbname: main, user: app}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="domain registry not found"):
        reset_db.load_database_targets(database_path, tmp_path / "missing.yaml")

    targets = reset_db.load_database_targets(
        database_path, tmp_path / "missing.yaml", default_only=True
    )
    assert [target.target_id for target in targets] == ["pg-a:5432/main"]


def test_validate_admin_bootstrap_rejects_placeholder_password(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(
        "bootstrap:\n  admin_password: change-me-on-first-login\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="admin_password"):
        reset_db.validate_admin_bootstrap(auth_path)


def test_validate_admin_bootstrap_accepts_configured_password(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(
        "bootstrap:\n  admin_password: a-private-password\n",
        encoding="utf-8",
    )

    reset_db.validate_admin_bootstrap(auth_path)


def test_validate_admin_bootstrap_rejects_non_string_password(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(
        "bootstrap:\n  admin_password: 12345678\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be a string"):
        reset_db.validate_admin_bootstrap(auth_path)


def test_build_truncate_statement_quotes_catalog_identifiers() -> None:
    statement = reset_db.build_truncate_statement(
        [("public", "asset_documents"), ("public", 'odd"table')]
    )

    assert statement == (
        'TRUNCATE TABLE "public"."asset_documents", '
        '"public"."odd""table" RESTART IDENTITY'
    )


class _Cursor:
    def __init__(
        self,
        rows: list[tuple[str, str]],
        sessions: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.rows = rows
        self.sessions = sessions or []
        self.executed: list[str] = []
        self._last_statement = ""

    def execute(self, statement: str) -> None:
        self.executed.append(statement)
        self._last_statement = statement

    def fetchall(self) -> list[tuple[object, ...]]:
        if "pg_stat_activity" in self._last_statement:
            return self.sessions
        return self.rows

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_truncate_public_tables_discovers_current_tables_dynamically() -> None:
    cursor = _Cursor(
        [("public", "asset_documents"), ("public", "serving_query_logs")]
    )

    tables = reset_db.truncate_public_tables(cursor)

    assert tables == ["public.asset_documents", "public.serving_query_logs"]
    assert "FROM pg_catalog.pg_class" in cursor.executed[0]
    assert "NOT c.relispartition" in cursor.executed[0]
    assert cursor.executed[1] == (
        'TRUNCATE TABLE "public"."asset_documents", '
        '"public"."serving_query_logs" RESTART IDENTITY'
    )


def test_truncate_public_tables_is_noop_when_database_has_no_tables() -> None:
    cursor = _Cursor([])

    tables = reset_db.truncate_public_tables(cursor)

    assert tables == []
    assert len(cursor.executed) == 1


def test_assert_no_other_sessions_rejects_online_writers() -> None:
    cursor = _Cursor(
        [],
        sessions=[(42, "app", "knowledge-mining", "10.0.0.4", "idle")],
    )

    with pytest.raises(RuntimeError, match="knowledge-mining"):
        reset_db.assert_no_other_sessions(cursor)


def test_truncate_public_tables_rejects_unknown_shared_database_table() -> None:
    cursor = _Cursor(
        [("public", "asset_documents"), ("public", "another_product_orders")]
    )

    with pytest.raises(ValueError, match="another_product_orders"):
        reset_db.truncate_public_tables(cursor)

    assert len(cursor.executed) == 1


def _install_fake_psycopg(monkeypatch: pytest.MonkeyPatch, cursor: _Cursor) -> None:
    module = SimpleNamespace(connect=lambda **_kwargs: _Connection(cursor))
    monkeypatch.setitem(__import__("sys").modules, "psycopg", module)


def _config() -> reset_db.DatabaseConfig:
    return reset_db.DatabaseConfig(
        host="pg.internal",
        port=5432,
        dbname="knowledge",
        user="app",
        password="secret",
    )


def test_main_defaults_to_dry_run(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    cursor = _Cursor([("public", "asset_documents")])
    _install_fake_psycopg(monkeypatch, cursor)
    monkeypatch.setattr(
        reset_db,
        "load_database_targets",
        lambda _database, _registry, **_kwargs: [_config()],
    )
    monkeypatch.setattr(reset_db, "validate_admin_bootstrap", lambda _path: None)

    result = reset_db.main([])

    assert result == 0
    assert len(cursor.executed) == 1
    assert "Dry run only" in capsys.readouterr().out


def test_main_execute_requires_exact_database_name(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    cursor = _Cursor([("public", "asset_documents")])
    _install_fake_psycopg(monkeypatch, cursor)
    monkeypatch.setattr(
        reset_db,
        "load_database_targets",
        lambda _database, _registry, **_kwargs: [_config()],
    )
    monkeypatch.setattr(reset_db, "validate_admin_bootstrap", lambda _path: None)
    monkeypatch.setattr("builtins.input", lambda _prompt: "YES")

    result = reset_db.main(["--execute"])

    assert result == 0
    assert not any("TRUNCATE TABLE" in item for item in cursor.executed)
    assert "Aborted" in capsys.readouterr().out


def test_main_execute_truncates_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    cursor = _Cursor(
        [("public", "asset_documents"), ("public", "serving_query_logs")]
    )
    _install_fake_psycopg(monkeypatch, cursor)
    monkeypatch.setattr(
        reset_db,
        "load_database_targets",
        lambda _database, _registry, **_kwargs: [_config()],
    )
    monkeypatch.setattr(reset_db, "validate_admin_bootstrap", lambda _path: None)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: "RESET pg.internal:5432/knowledge"
    )

    result = reset_db.main(["--execute"])

    assert result == 0
    assert any("SET LOCAL lock_timeout" in item for item in cursor.executed)
    assert any("SET LOCAL statement_timeout" in item for item in cursor.executed)
    assert any("TRUNCATE TABLE" in item for item in cursor.executed)
    output = capsys.readouterr().out
    assert "schema was preserved" in output
    assert "MinIO object bytes and Docker volumes were not modified" in output
    assert "PostgreSQL upload/object metadata was cleared" in output


def test_main_preflights_every_target_before_clearing_any(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _config()
    second = reset_db.DatabaseConfig(
        host="pg-b.internal", port=5432, dbname="domain_b", user="app"
    )
    _install_fake_psycopg(monkeypatch, _Cursor([]))
    monkeypatch.setattr(
        reset_db,
        "load_database_targets",
        lambda _database, _registry, **_kwargs: [first, second],
    )
    monkeypatch.setattr(reset_db, "validate_admin_bootstrap", lambda _path: None)
    inspected: list[str] = []
    cleared: list[str] = []

    def inspect(_psycopg, config, *, require_quiet):
        inspected.append(config.target_id)
        assert require_quiet is True
        if config is second:
            raise RuntimeError("active session")
        return [("public", "asset_documents")]

    monkeypatch.setattr(reset_db, "_inspect_target", inspect)
    monkeypatch.setattr(
        reset_db,
        "_clear_target",
        lambda _psycopg, config: cleared.append(config.target_id),
    )

    result = reset_db.main(["--execute"])

    assert result == 1
    assert inspected == [first.target_id, second.target_id]
    assert cleared == []
