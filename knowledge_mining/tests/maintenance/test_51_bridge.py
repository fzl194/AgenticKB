from __future__ import annotations

import pytest
import inspect
from pathlib import Path

from knowledge_mining.mining.maintenance.database_upgrade.bridge_51 import (
    Bridge51Error,
    _backfill_mcp_keys,
    _backfill_user_domains,
    _validate_legacy_key_coverage,
    derive_user_domains,
    derive_legacy_key_domain,
)
from knowledge_mining.mining.maintenance.database_upgrade import __main__ as upgrade_main
from knowledge_mining.mining.maintenance.database_upgrade.manifest import load_manifest


class _Rows:
    def __init__(self, rows=(), rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _UserDomainConnection:
    def __init__(self):
        self.inserted: list[tuple[str, str]] = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        if sql.startswith("SELECT id, username, site_role"):
            return _Rows([("u-1", "alice", "member")])
        if sql.startswith("SELECT DISTINCT u.id, kb.domain"):
            return _Rows([("u-1", "generic"), ("u-1", "odn")])
        if sql.startswith("SELECT user_id, domain FROM user_domains"):
            return _Rows([("u-1", "generic")])
        if sql.startswith("INSERT INTO user_domains"):
            self.inserted.append(params)
            return _Rows(rowcount=0)
        if sql.startswith("SELECT count(*) FROM kb_users"):
            return _Rows([(0,)])
        raise AssertionError(sql)


def test_legacy_key_domain_uses_the_single_active_open_kb_domain() -> None:
    assert derive_legacy_key_domain(
        open_kbs=[
            ("kb-1", "odn", "active"),
            ("kb-3", "odn", "active"),
            ("kb-4", "civil", "deleted"),
        ],
        bindings=["generic"],
        fallback_domain=None,
        is_admin=False,
        default_domain="cloud_core_network",
    ) == ("odn", "open-kbs")


def test_legacy_key_domain_rejects_cross_domain_open_grants() -> None:
    with pytest.raises(Bridge51Error, match="跨多个 domain"):
        derive_legacy_key_domain(
            open_kbs=[("kb-1", "odn", "active"), ("kb-2", "generic", "active")],
            bindings=[],
            fallback_domain=None,
            is_admin=False,
            default_domain="cloud_core_network",
        )


def test_legacy_key_domain_uses_binding_then_explicit_fallback() -> None:
    assert derive_legacy_key_domain(
        open_kbs=[],
        bindings=[],
        fallback_domain="generic",
        is_admin=False,
        default_domain="cloud_core_network",
    ) == ("generic", "fallback")
    assert derive_legacy_key_domain(
        open_kbs=[],
        bindings=["odn"],
        fallback_domain=None,
        is_admin=False,
        default_domain="cloud_core_network",
    ) == ("odn", "user_domains")


def test_legacy_admin_without_open_kbs_uses_registry_default() -> None:
    assert derive_legacy_key_domain(
        open_kbs=[],
        bindings=[],
        fallback_domain=None,
        is_admin=True,
        default_domain="cloud_core_network",
    ) == ("cloud_core_network", "admin-default")


def test_legacy_member_without_any_domain_fails_closed() -> None:
    with pytest.raises(Bridge51Error, match="无法推导"):
        derive_legacy_key_domain(
            open_kbs=[],
            bindings=[],
            fallback_domain=None,
            is_admin=False,
            default_domain="cloud_core_network",
        )


def test_legacy_key_without_open_grants_rejects_multiple_bindings() -> None:
    with pytest.raises(Bridge51Error, match="多个 domain"):
        derive_legacy_key_domain(
            open_kbs=[],
            bindings=["generic", "odn"],
            fallback_domain=None,
            is_admin=False,
            default_domain="cloud_core_network",
        )


def test_explicit_fallback_resolves_multiple_bindings_consistently() -> None:
    assert derive_legacy_key_domain(
        open_kbs=[],
        bindings=["generic", "odn"],
        fallback_domain="odn",
        is_admin=False,
        default_domain="cloud_core_network",
    ) == ("odn", "fallback")


def test_existing_user_domain_bindings_are_authoritative_and_not_expanded() -> None:
    assert derive_user_domains(
        existing=["generic"],
        owned_or_member=["generic", "odn"],
        fallback_domain="cloud_core_network",
        existing_is_authoritative=True,
    ) == ["generic"]

    connection = _UserDomainConnection()
    _backfill_user_domains(
        connection,
        fallback_domain="cloud_core_network",
        allowed_domains=frozenset({"generic", "odn", "cloud_core_network"}),
        existing_is_authoritative=True,
    )
    assert connection.inserted == [("u-1", "generic")]


def test_zero_binding_user_infers_owned_domains_then_default() -> None:
    assert derive_user_domains(
        existing=[],
        owned_or_member=["odn", "generic"],
        fallback_domain="cloud_core_network",
        existing_is_authoritative=False,
    ) == ["generic", "odn"]
    assert derive_user_domains(
        existing=[],
        owned_or_member=[],
        fallback_domain="cloud_core_network",
        existing_is_authoritative=False,
    ) == ["cloud_core_network"]


def test_partial_51_user_domain_rows_are_completed_from_membership() -> None:
    assert derive_user_domains(
        existing=["generic"],
        owned_or_member=["generic", "odn"],
        fallback_domain="cloud_core_network",
        existing_is_authoritative=False,
    ) == ["generic", "odn"]


def test_upgrade_plan_runs_51_authorization_preflight_before_downtime() -> None:
    source = inspect.getsource(upgrade_main._plan)
    assert source.index("validate_supported_rebase_source") < source.index(
        "preflight_51_bridge"
    )


def test_upgrade_apply_runs_51_bridge_before_52_manifest() -> None:
    source = inspect.getsource(upgrade_main._apply)
    assert source.index("preflight_51_bridge") < source.index("_target_endpoint")
    assert source.index("apply_51_bridge") < source.index("apply_manifest", source.index("target ="))


def test_51_bridge_is_first_versioned_migration_and_pins_all_four_tables() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    manifest = load_manifest(repo_root / "databases/migrations/manifest.yaml")
    first = manifest.migrations[0]
    sql = first.path.read_text(encoding="utf-8")

    assert first.migration_id == "core/20260918_bridge_51_tables"
    for table in ("kb_purge_tasks", "user_domains", "mcp_keys", "mcp_key_open_kbs"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_mcp_bridge_preserves_timestamps_and_grant_time_without_tool_expansion() -> None:
    source = inspect.getsource(_backfill_mcp_keys)

    for field in ("created_at", "rotated_at", "last_used_at", "granted_at"):
        assert field in source
    assert "normalize_legacy_open_tools" not in source
    assert "uuid.uuid5" in source


def test_51_operational_drift_uses_owner_lineage_not_frozen_key_hash() -> None:
    source = inspect.getsource(_validate_legacy_key_coverage)

    assert "new_key.user_id = old_key.user_id" in source
    assert "new_key.key_hash = old_key.key_hash" not in source


def test_legacy_51_one_off_scripts_are_removed() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    for name in (
        "backfill_user_domains.py",
        "backfill_mcp_keys.py",
        "drop_legacy_mcp_tables.py",
    ):
        assert not (repo_root / name).exists()
