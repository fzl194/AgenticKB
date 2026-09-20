from __future__ import annotations

import pytest
import inspect
from pathlib import Path

from knowledge_mining.mining.maintenance.database_upgrade.bridge_51 import (
    Bridge51Error,
    Bridge51Policy,
    Bridge51PreflightReport,
    _backfill_mcp_keys,
    _backfill_user_domains,
    _normalize_bridge_open_tools,
    _validate_legacy_key_coverage,
    derive_user_domains,
    derive_legacy_key_domain,
    preflight_51_bridge,
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


class _PreflightConnection:
    def __init__(
        self,
        *,
        owned=(("u-1", "generic"),),
        tools=("search_knowledge",),
        open_rows=(("u-1", "kb-1", "generic", "KB 1", "active"),),
    ):
        self.tables = {"mcp_access", "mcp_open_kbs"}
        self.owned = list(owned)
        self.tools = list(tools)
        self.open_rows = list(open_rows)

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        if sql.startswith("SELECT to_regclass"):
            table = str(params[0]).removeprefix("public.")
            return _Rows([(table in self.tables,)])
        if sql.startswith("SELECT DISTINCT domain FROM knowledge_bases"):
            return _Rows([("generic",)])
        if sql.startswith("SELECT DISTINCT u.id, kb.domain"):
            return _Rows(self.owned)
        if sql.startswith("SELECT id, username FROM kb_users"):
            return _Rows([("u-1", "alice")])
        if sql.startswith("SELECT a.user_id, u.username"):
            return _Rows([("u-1", "alice", "member", "kbm_1234", self.tools)])
        if sql.startswith("SELECT o.user_id, o.kb_id"):
            return _Rows(self.open_rows)
        if sql.startswith("SELECT key_hash FROM mcp_access"):
            return _Rows([])
        raise AssertionError(sql)


_POLICY = Bridge51Policy("generic", frozenset({"generic", "odn"}))


def test_legacy_key_domain_uses_the_single_active_open_kb_domain() -> None:
    assert derive_legacy_key_domain(
        open_kbs=[
            ("kb-1", "odn", "active"),
            ("kb-3", "odn", "active"),
            ("kb-4", "civil", "deleted"),
        ],
        bindings=["generic"],
        is_admin=False,
        default_domain="cloud_core_network",
    ) == ("odn", "open-kbs")


def test_legacy_key_domain_rejects_cross_domain_open_grants() -> None:
    with pytest.raises(Bridge51Error, match="跨多个 domain"):
        derive_legacy_key_domain(
            open_kbs=[("kb-1", "odn", "active"), ("kb-2", "generic", "active")],
            bindings=[],
            is_admin=False,
            default_domain="cloud_core_network",
        )


def test_legacy_key_domain_uses_single_existing_binding() -> None:
    assert derive_legacy_key_domain(
        open_kbs=[],
        bindings=["odn"],
        is_admin=False,
        default_domain="cloud_core_network",
    ) == ("odn", "user_domains")


def test_legacy_admin_without_open_kbs_uses_registry_default() -> None:
    assert derive_legacy_key_domain(
        open_kbs=[],
        bindings=[],
        is_admin=True,
        default_domain="cloud_core_network",
    ) == ("cloud_core_network", "admin-default")


def test_legacy_member_without_any_domain_fails_closed() -> None:
    with pytest.raises(Bridge51Error, match="无法推导"):
        derive_legacy_key_domain(
            open_kbs=[],
            bindings=[],
            is_admin=False,
            default_domain="cloud_core_network",
        )


def test_legacy_key_without_open_grants_rejects_multiple_bindings() -> None:
    with pytest.raises(Bridge51Error, match="多个 domain"):
        derive_legacy_key_domain(
            open_kbs=[],
            bindings=["generic", "odn"],
            is_admin=False,
            default_domain="cloud_core_network",
        )


def test_existing_user_domain_bindings_are_authoritative_and_not_expanded() -> None:
    assert derive_user_domains(
        existing=["generic"],
        owned_or_member=["generic", "odn"],
        existing_is_authoritative=True,
    ) == ["generic"]

    connection = _UserDomainConnection()
    _backfill_user_domains(
        connection,
        allowed_domains=frozenset({"generic", "odn", "cloud_core_network"}),
        existing_is_authoritative=True,
    )
    assert connection.inserted == [("u-1", "generic")]


def test_zero_binding_user_infers_owned_domains_but_never_gets_default_grant() -> None:
    assert derive_user_domains(
        existing=[],
        owned_or_member=["odn", "generic"],
        existing_is_authoritative=False,
    ) == ["generic", "odn"]
    assert derive_user_domains(
        existing=[],
        owned_or_member=[],
        existing_is_authoritative=False,
    ) == []


def test_partial_51_user_domain_rows_are_completed_from_membership() -> None:
    assert derive_user_domains(
        existing=["generic"],
        owned_or_member=["generic", "odn"],
        existing_is_authoritative=False,
    ) == ["generic", "odn"]


def test_upgrade_plan_runs_51_authorization_preflight_before_downtime() -> None:
    source = inspect.getsource(upgrade_main._plan)
    assert source.index("validate_supported_rebase_source") < source.index(
        "preflight_51_bridge"
    )
    assert "bridge_preflight" in source


def test_clean_preflight_report_exposes_all_four_zero_invariants() -> None:
    report = Bridge51PreflightReport.clean(legacy_source=True)

    assert report.to_dict() == {
        "legacy_source": True,
        "cross_domain_keys": 0,
        "zero_binding_users": 0,
        "retired_tool_config_keys": 0,
        "discarded_open_grants": 0,
    }


def test_preflight_clean_legacy_source_reports_all_zero() -> None:
    assert preflight_51_bridge(_PreflightConnection(), _POLICY).to_dict() == {
        "legacy_source": True,
        "cross_domain_keys": 0,
        "zero_binding_users": 0,
        "retired_tool_config_keys": 0,
        "discarded_open_grants": 0,
    }


@pytest.mark.parametrize(
    ("connection", "message"),
    [
        (_PreflightConnection(owned=()), "零绑定用户"),
        (_PreflightConnection(tools=("retired_tool",)), "当前三类工具"),
        (_PreflightConnection(open_rows=(
            ("u-1", "kb-1", "generic", "KB 1", "active"),
            ("u-1", "kb-2", "odn", "KB 2", "active"),
        )), "跨多个 domain"),
        (_PreflightConnection(open_rows=(
            ("u-1", "kb-1", "generic", "KB 1", "deleted"),
        )), "会被丢弃"),
    ],
)
def test_preflight_rejects_every_permission_guess(connection, message) -> None:
    with pytest.raises(Bridge51Error, match=message):
        preflight_51_bridge(connection, _POLICY)


def test_bridge_tool_normalization_only_emits_current_three_tools() -> None:
    assert _normalize_bridge_open_tools(None) is None
    assert _normalize_bridge_open_tools(["search_knowledge"]) == ["search_knowledge"]
    assert _normalize_bridge_open_tools(["get_content", "get_evidence"]) == [
        "get_knowledge"
    ]
    assert _normalize_bridge_open_tools(["retired_tool_name"]) == []


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


def test_mcp_bridge_preserves_timestamps_and_never_silently_drops_grants() -> None:
    source = inspect.getsource(_backfill_mcp_keys)

    for field in ("created_at", "rotated_at", "last_used_at", "granted_at"):
        assert field in source
    assert "_normalize_bridge_open_tools" in source
    assert "dropped.append" not in source
    assert "拒绝删除旧开放库授权" in source
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
