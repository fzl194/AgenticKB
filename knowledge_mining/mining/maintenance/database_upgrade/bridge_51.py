"""Compatibility bridge for production databases that have not deployed design 51.

The bridge runs only on the freshly cloned target.  It reproduces the approved
51 backfill rules before the 52 convergence migrations remove legacy MCP data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from typing import Any
import uuid

from psycopg.types.json import Jsonb
import yaml

from knowledge_mining.mining.infra.pg_schema import _execute_ddl


class Bridge51Error(RuntimeError):
    """Legacy authorization data cannot be mapped without guessing."""


@dataclass(frozen=True, slots=True)
class Bridge51Report:
    user_domain_rows: int
    migrated_keys: int
    migrated_grants: int
    dropped_cross_domain_grants: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class Bridge51Policy:
    default_domain: str
    fallback_domain: str | None
    allowed_domains: frozenset[str]


def load_bridge_51_policy(config_dir: Path) -> Bridge51Policy:
    raw = yaml.safe_load(
        (config_dir / "domain_registry.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(raw, dict) or not isinstance(raw.get("domains"), dict):
        raise Bridge51Error("domain_registry.yaml 缺少 domains")
    default_domain = str(raw.get("default_domain") or "").strip()
    allowed = frozenset(
        str(domain)
        for domain, entry in raw["domains"].items()
        if isinstance(entry, dict) and entry.get("enabled", True) is not False
    )
    if not default_domain or default_domain not in allowed:
        raise Bridge51Error("default_domain 不存在或未启用")
    fallback = os.getenv("CMKB_MIGRATION_FALLBACK_DOMAIN")
    if fallback and fallback not in allowed:
        raise Bridge51Error(f"CMKB_MIGRATION_FALLBACK_DOMAIN 未启用：{fallback}")
    return Bridge51Policy(default_domain, fallback, allowed)


def derive_legacy_key_domain(
    *,
    open_kbs: list[tuple[str, str, str]],
    bindings: list[str],
    fallback_domain: str | None,
    is_admin: bool,
    default_domain: str,
) -> tuple[str, str]:
    active_domains = sorted({domain for _, domain, status in open_kbs if status == "active"})
    if len(active_domains) > 1:
        raise Bridge51Error("旧 MCP 钥匙开放库跨多个 domain，无法无损自动迁移")
    if active_domains:
        return active_domains[0], "open-kbs"
    if fallback_domain:
        return fallback_domain, "fallback"
    normalized_bindings = sorted(set(bindings))
    if len(normalized_bindings) > 1:
        raise Bridge51Error("旧 MCP 钥匙无开放库但用户绑定多个 domain，无法自动选择")
    if normalized_bindings:
        return normalized_bindings[0], "user_domains"
    if is_admin and default_domain:
        return default_domain, "admin-default"
    raise Bridge51Error("旧 MCP 钥匙无法推导唯一 domain")


def derive_user_domains(
    *,
    existing: list[str],
    owned_or_member: list[str],
    fallback_domain: str,
    existing_is_authoritative: bool,
) -> list[str]:
    existing_domains = sorted(set(existing))
    if existing_domains and existing_is_authoritative:
        return existing_domains
    inferred = sorted(set(existing_domains) | set(owned_or_member))
    return inferred or [fallback_domain]


def _table_exists(connection: Any, table: str) -> bool:
    row = connection.execute(
        "SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)
    ).fetchone()
    return bool(row and row[0])


def _validate_new_mcp_scope(
    connection: Any, allowed_domains: frozenset[str]
) -> None:
    if not _table_exists(connection, "mcp_keys"):
        return
    key_domains = {
        str(row[0]) for row in connection.execute("SELECT DISTINCT domain FROM mcp_keys").fetchall()
    }
    unknown = sorted(key_domains - allowed_domains)
    if unknown:
        raise Bridge51Error(f"mcp_keys 含未知或禁用 domain：{unknown}")
    cross_domain = connection.execute(
        """SELECT 1 FROM mcp_key_open_kbs grant_row
             JOIN mcp_keys key_row ON key_row.id = grant_row.key_id
             JOIN knowledge_bases kb ON kb.id = grant_row.kb_id
            WHERE kb.domain IS DISTINCT FROM key_row.domain LIMIT 1"""
    ).fetchone()
    if cross_domain:
        raise Bridge51Error("mcp_key_open_kbs 含跨 domain 授权")


def _validate_legacy_key_coverage(connection: Any) -> None:
    """Ensure every legacy owner reached the authoritative 51 key model.

    Do not compare key_hash/status/tools: rotate and revoke legitimately change
    those fields after the one-off 51 backfill while the legacy copy stays
    frozen.  Ownership is the stable lineage that detects a partial backfill.
    """
    missing = connection.execute(
        """SELECT 1 FROM mcp_access old_key
             WHERE NOT EXISTS (
                 SELECT 1 FROM mcp_keys new_key
                  WHERE new_key.user_id = old_key.user_id)
             LIMIT 1"""
    ).fetchone()
    if missing:
        raise Bridge51Error("51 新钥匙表只迁了一部分旧用户，拒绝猜测续跑")


def preflight_51_bridge(connection: Any, policy: Bridge51Policy) -> None:
    old_access = _table_exists(connection, "mcp_access")
    old_open = _table_exists(connection, "mcp_open_kbs")
    new_keys = _table_exists(connection, "mcp_keys")
    new_open = _table_exists(connection, "mcp_key_open_kbs")
    if old_access != old_open or new_keys != new_open:
        raise Bridge51Error("MCP 表处于半升级状态，拒绝猜测恢复")
    active_kb_domains = {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT domain FROM knowledge_bases WHERE status = 'active'"
        ).fetchall()
    }
    unknown_kb_domains = sorted(active_kb_domains - policy.allowed_domains)
    if unknown_kb_domains:
        raise Bridge51Error(
            f"active KB 属于未知或禁用 domain：{unknown_kb_domains}"
        )
    if new_keys and new_open:
        _validate_new_mcp_scope(connection, policy.allowed_domains)
    existing_binding_rows: list[tuple[Any, Any]] = []
    if _table_exists(connection, "user_domains"):
        existing_binding_rows = connection.execute(
            "SELECT user_id, domain FROM user_domains"
        ).fetchall()
        unknown_bindings = sorted(
            {str(domain) for _, domain in existing_binding_rows}
            - policy.allowed_domains
        )
        if unknown_bindings:
            raise Bridge51Error(
                f"user_domains 含未知或禁用 domain：{unknown_bindings}"
            )
    if new_keys and new_open:
        if old_access:
            _validate_legacy_key_coverage(connection)
        return
    if not old_access:
        return

    access_users = connection.execute(
        """SELECT a.user_id, u.username, u.site_role
             FROM mcp_access a LEFT JOIN kb_users u ON u.id = a.user_id"""
    ).fetchall()
    if any(username is None for _, username, _ in access_users):
        raise Bridge51Error("旧 MCP 钥匙存在孤儿用户")
    open_rows = connection.execute(
        """SELECT o.user_id, kb.domain
             FROM mcp_open_kbs o JOIN knowledge_bases kb ON kb.id = o.kb_id
            WHERE kb.status = 'active'"""
    ).fetchall()
    open_domains: dict[str, set[str]] = {}
    for user_id, domain in open_rows:
        open_domains.setdefault(str(user_id), set()).add(str(domain))
    if any(len(domains) > 1 for domains in open_domains.values()):
        raise Bridge51Error("旧 MCP 钥匙开放库跨多个 domain，需显式处理后再迁移")
    unknown_open = sorted(
        {domain for domains in open_domains.values() for domain in domains}
        - policy.allowed_domains
    )
    if unknown_open:
        raise Bridge51Error(f"旧 MCP 开放库属于未知或禁用 domain：{unknown_open}")

    owned_rows = connection.execute(
        """SELECT DISTINCT u.id, kb.domain
             FROM kb_users u JOIN knowledge_bases kb
               ON (kb.owner_id = u.id OR EXISTS (
                   SELECT 1 FROM kb_members m
                    WHERE m.kb_id = kb.id AND m.user_id = u.id))
            WHERE kb.status = 'active'"""
    ).fetchall()
    bindings: dict[str, set[str]] = {}
    for user_id, domain in owned_rows:
        bindings.setdefault(str(user_id), set()).add(str(domain))
    for user_id, domain in existing_binding_rows:
        bindings.setdefault(str(user_id), set()).add(str(domain))
    duplicate_hash = connection.execute(
        """SELECT key_hash FROM mcp_access GROUP BY key_hash
             HAVING count(*) > 1 LIMIT 1"""
    ).fetchone()
    if duplicate_hash:
        raise Bridge51Error("旧 MCP 存在重复 key_hash，无法安全迁移")
    for user_id, username, site_role in access_users:
        if open_domains.get(str(user_id)) or site_role == "admin":
            continue
        candidates = bindings.get(str(user_id), set())
        if len(candidates) > 1 and not policy.fallback_domain:
            raise Bridge51Error(
                f"用户 {username!r} 的旧 MCP 钥匙无开放库且候选 domain 超过一个"
            )


def _ensure_51_tables(connection: Any, repo_root: Path) -> None:
    for relative_path in (
        "databases/kb/schemas/013_kb_purge_tasks.sql",
        "databases/kb/schemas/013_user_domains.sql",
        "databases/kb/schemas/014_mcp_keys.sql",
    ):
        ddl = (repo_root / relative_path).read_text(encoding="utf-8")
        _execute_ddl(connection, ddl, transactional=True)


def _backfill_user_domains(
    connection: Any,
    *,
    fallback_domain: str,
    allowed_domains: frozenset[str],
    existing_is_authoritative: bool,
) -> int:
    users = connection.execute(
        """SELECT id, username, site_role FROM kb_users
             WHERE status = 'active' ORDER BY username"""
    ).fetchall()
    rows = connection.execute(
        """SELECT DISTINCT u.id, kb.domain
             FROM kb_users u
             JOIN knowledge_bases kb
               ON (kb.owner_id = u.id OR EXISTS (
                   SELECT 1 FROM kb_members m
                    WHERE m.kb_id = kb.id AND m.user_id = u.id))
            WHERE u.status = 'active' AND kb.status = 'active'"""
    ).fetchall()
    domains_by_user: dict[str, list[str]] = {}
    for user_id, domain in rows:
        domains_by_user.setdefault(str(user_id), []).append(str(domain))
    existing_by_user: dict[str, list[str]] = {}
    for user_id, domain in connection.execute(
        "SELECT user_id, domain FROM user_domains ORDER BY user_id, domain"
    ).fetchall():
        existing_by_user.setdefault(str(user_id), []).append(str(domain))
    if fallback_domain not in allowed_domains:
        raise Bridge51Error(f"fallback domain 未启用或不存在：{fallback_domain}")

    inserted = 0
    for user_id, username, site_role in users:
        if site_role == "admin":
            continue
        domains = derive_user_domains(
            existing=existing_by_user.get(str(user_id), []),
            owned_or_member=domains_by_user.get(str(user_id), []),
            fallback_domain=fallback_domain,
            existing_is_authoritative=existing_is_authoritative,
        )
        if not domains:
            domains = [fallback_domain]
        unknown = sorted(set(domains) - allowed_domains)
        if unknown:
            raise Bridge51Error(f"用户 {username!r} 绑定未知或禁用 domain：{unknown}")
        for domain in domains:
            cursor = connection.execute(
                """INSERT INTO user_domains (user_id, domain) VALUES (%s, %s)
                   ON CONFLICT (user_id, domain) DO NOTHING""",
                (user_id, domain),
            )
            inserted += max(int(cursor.rowcount or 0), 0)
    missing = connection.execute(
        """SELECT count(*) FROM kb_users u
             WHERE u.status = 'active' AND u.site_role <> 'admin'
               AND NOT EXISTS (
                   SELECT 1 FROM user_domains ud WHERE ud.user_id = u.id)"""
    ).fetchone()
    if missing and int(missing[0]) > 0:
        raise Bridge51Error(f"仍有 {missing[0]} 个普通用户没有 domain 绑定")
    return inserted


def _backfill_mcp_keys(
    connection: Any,
    *,
    default_domain: str,
    fallback_domain: str | None,
    allowed_domains: frozenset[str],
    new_tables_authoritative: bool,
) -> tuple[int, int, tuple[tuple[str, str, str], ...]]:
    access_exists = _table_exists(connection, "mcp_access")
    open_exists = _table_exists(connection, "mcp_open_kbs")
    if access_exists != open_exists:
        raise Bridge51Error("旧 MCP 表处于只剩一张的异常状态")
    if not access_exists:
        return 0, 0, ()
    if new_tables_authoritative:
        _validate_legacy_key_coverage(connection)
        return 0, 0, ()

    connection.execute("ALTER TABLE mcp_access ADD COLUMN IF NOT EXISTS open_tools JSONB")
    connection.execute("ALTER TABLE mcp_access ADD COLUMN IF NOT EXISTS instructions TEXT")
    connection.execute(
        "ALTER TABLE mcp_access ADD COLUMN IF NOT EXISTS tool_descriptions JSONB"
    )
    access_rows = connection.execute(
        """SELECT a.user_id, a.key_hash, a.key_prefix, a.status,
                  a.open_tools, a.instructions, a.tool_descriptions,
                  a.created_at, a.rotated_at, a.last_used_at,
                  u.username, u.site_role
             FROM mcp_access a
             LEFT JOIN kb_users u ON u.id = a.user_id
            ORDER BY u.username NULLS LAST, a.user_id"""
    ).fetchall()
    open_rows = connection.execute(
        """SELECT o.user_id, o.kb_id, kb.domain, kb.status, o.granted_at
             FROM mcp_open_kbs o
             JOIN knowledge_bases kb ON kb.id = o.kb_id"""
    ).fetchall()
    open_by_user: dict[str, list[tuple[str, str, str, Any]]] = {}
    for user_id, kb_id, domain, status, granted_at in open_rows:
        open_by_user.setdefault(str(user_id), []).append(
            (str(kb_id), str(domain), str(status), granted_at)
        )
    binding_rows = connection.execute(
        "SELECT user_id, domain FROM user_domains ORDER BY user_id, domain"
    ).fetchall()
    bindings_by_user: dict[str, list[str]] = {}
    for user_id, domain in binding_rows:
        bindings_by_user.setdefault(str(user_id), []).append(str(domain))

    migrated_keys = 0
    migrated_grants = 0
    dropped: list[tuple[str, str, str]] = []
    for row in access_rows:
        (
            user_id,
            key_hash,
            key_prefix,
            status,
            open_tools,
            instructions,
            tool_descriptions,
            created_at,
            rotated_at,
            last_used_at,
            username,
            site_role,
        ) = row
        if username is None:
            raise Bridge51Error(f"旧 MCP 钥匙引用不存在的用户：{user_id}")
        user_open = open_by_user.get(str(user_id), [])
        domain, domain_source = derive_legacy_key_domain(
            open_kbs=[(kb_id, kb_domain, kb_status)
                      for kb_id, kb_domain, kb_status, _ in user_open],
            bindings=bindings_by_user.get(str(user_id), []),
            fallback_domain=fallback_domain,
            is_admin=site_role == "admin",
            default_domain=default_domain,
        )
        if domain not in allowed_domains:
            raise Bridge51Error(f"旧 MCP 钥匙推导到未知或禁用 domain：{domain}")
        if site_role != "admin":
            connection.execute(
                """INSERT INTO user_domains (user_id, domain) VALUES (%s, %s)
                   ON CONFLICT DO NOTHING""",
                (user_id, domain),
            )
        key_id = uuid.uuid5(uuid.NAMESPACE_URL, f"agentickb:mcp:{key_hash}").hex
        normalized_tools = open_tools
        existing = connection.execute(
            """SELECT id, user_id, domain, key_prefix, status, open_tools,
                      instructions, tool_descriptions, created_at, rotated_at, last_used_at
                 FROM mcp_keys WHERE key_hash = %s""",
            (key_hash,),
        ).fetchone()
        if existing:
            expected = (
                user_id, domain, key_prefix, status, instructions, tool_descriptions,
            )
            actual = (
                existing[1], existing[2], existing[3], existing[4],
                existing[6], existing[7],
            )
            if actual != expected:
                raise Bridge51Error(f"已存在 key_hash 的字段与旧钥匙不一致：{username}")
            key_id = str(existing[0])
            # Repair the old 51 backfill: it did not preserve timestamps and
            # could normalize an all-retired tool list to NULL (= allow all).
            connection.execute(
                """UPDATE mcp_keys SET open_tools = %s,
                          created_at = %s, rotated_at = %s, last_used_at = %s
                     WHERE id = %s""",
                (
                    Jsonb(open_tools) if open_tools is not None else None,
                    created_at,
                    rotated_at,
                    last_used_at,
                    key_id,
                ),
            )
        inserted = False
        name = "default"
        with connection.transaction():
            if not existing:
                for attempt in range(10):
                    cursor = connection.execute(
                    """INSERT INTO mcp_keys
                         (id, user_id, name, domain, key_hash, key_prefix, status,
                          open_tools, instructions, tool_descriptions,
                          created_at, rotated_at, last_used_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (user_id, domain, name) WHERE status = 'active'
                       DO NOTHING RETURNING id""",
                    (
                        key_id,
                        user_id,
                        name,
                        domain,
                        key_hash,
                        key_prefix,
                        status,
                        Jsonb(normalized_tools) if normalized_tools is not None else None,
                        instructions,
                        Jsonb(tool_descriptions) if tool_descriptions is not None else None,
                        created_at,
                        rotated_at,
                        last_used_at,
                    ),
                    )
                    if cursor.fetchone() is not None:
                        inserted = True
                        break
                    name = f"default-{attempt + 2}"
                if not inserted:
                    raise Bridge51Error(f"用户 {username!r} 的 legacy 钥匙名称冲突超过10次")
            for kb_id, kb_domain, kb_status, granted_at in user_open:
                if kb_domain == domain and kb_status == "active":
                    cursor = connection.execute(
                        """INSERT INTO mcp_key_open_kbs (key_id, kb_id, granted_at)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (key_id, kb_id) DO UPDATE
                           SET granted_at = EXCLUDED.granted_at""",
                        (key_id, kb_id, granted_at),
                    )
                    migrated_grants += max(int(cursor.rowcount or 0), 0)
                else:
                    dropped.append((str(username), kb_id, kb_domain))
        if not existing:
            migrated_keys += 1
    return migrated_keys, migrated_grants, tuple(dropped)


def apply_51_bridge(
    connection: Any,
    *,
    repo_root: Path,
    default_domain: str,
    fallback_domain: str | None = None,
    allowed_domains: frozenset[str],
) -> Bridge51Report:
    old_access = _table_exists(connection, "mcp_access")
    old_open = _table_exists(connection, "mcp_open_kbs")
    new_keys = _table_exists(connection, "mcp_keys")
    new_open = _table_exists(connection, "mcp_key_open_kbs")
    if old_access != old_open or new_keys != new_open:
        raise Bridge51Error("MCP 表处于半升级状态，拒绝猜测恢复")
    purge_exists = _table_exists(connection, "kb_purge_tasks")
    if not purge_exists:
        deleting = connection.execute(
            "SELECT count(*) FROM knowledge_bases WHERE status = 'deleting'"
        ).fetchone()
        if deleting and int(deleting[0]) > 0:
            raise Bridge51Error("存在 deleting KB 但 kb_purge_tasks 缺失，无法恢复删除进度")
    _ensure_51_tables(connection, repo_root)
    _validate_new_mcp_scope(connection, allowed_domains)
    with connection.transaction():
        domain_rows = _backfill_user_domains(
            connection,
            fallback_domain=fallback_domain or default_domain,
            allowed_domains=allowed_domains,
            existing_is_authoritative=new_keys and new_open,
        )
        invalid_domains = connection.execute(
            "SELECT DISTINCT domain FROM user_domains"
        ).fetchall()
        unknown = sorted(
            str(row[0]) for row in invalid_domains if str(row[0]) not in allowed_domains
        )
        if unknown:
            raise Bridge51Error(f"user_domains 含未知或禁用 domain：{unknown}")
    keys, grants, dropped = _backfill_mcp_keys(
        connection,
        default_domain=default_domain,
        fallback_domain=fallback_domain,
        allowed_domains=allowed_domains,
        new_tables_authoritative=new_keys and new_open,
    )
    return Bridge51Report(domain_rows, keys, grants, dropped)


__all__ = [
    "Bridge51Error",
    "Bridge51Report",
    "Bridge51Policy",
    "apply_51_bridge",
    "derive_legacy_key_domain",
    "derive_user_domains",
    "load_bridge_51_policy",
    "preflight_51_bridge",
]
