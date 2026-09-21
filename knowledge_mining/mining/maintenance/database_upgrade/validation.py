"""Post-migration structural verification."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from psycopg import sql

from .contract import (
    EXPECTED_FORMAL_TABLES,
    EXPECTED_PHYSICAL_TABLES,
    BRIDGE_51_TABLES,
    FORMAL_TABLES,
    MIGRATION_LEDGER_TABLE,
    LEGACY_COMPAT_TABLES,
    PRE51_REQUIRED_TABLES,
    RETIRED_TABLES,
)
from .manifest import MigrationManifest, pending_migrations
from .ledger import load_applied, schema_complete
from .manifest import manifest_checksum


class SchemaValidationError(RuntimeError):
    """The target database does not match the converged schema contract."""


@dataclass(frozen=True, slots=True)
class SchemaValidationReport:
    public_tables: tuple[str, ...]
    extensions: tuple[str, ...]
    schema_version: str


REQUIRED_INDEXES: dict[str, tuple[bool, tuple[str, ...]]] = {
    "idx_user_domains_domain": (False, ("user_domains", "domain")),
    "idx_mcp_keys_user": (False, ("mcp_keys", "user_id")),
    "idx_mcp_keys_hash": (True, ("mcp_keys", "key_hash")),
    "idx_mcp_keys_active_name": (
        True,
        ("mcp_keys", "user_id", "domain", "name", "where", "status", "active"),
    ),
}


def expected_rebase_target_tables(source_tables: set[str]) -> set[str]:
    del source_tables
    return set(FORMAL_TABLES)


# 001 迁移合法改写 mining_runs（删除 subloop_stage/ontology_version_id 两列，
# 并把 awaiting_review 状态改写为 interrupted）：共享列内容指纹对这张表失真，
# 只保留行数比对；其余保留表仍做逐行内容校验。
CONTENT_DIGEST_EXEMPT_TABLES: frozenset[str] = frozenset({"mining_runs"})


def compare_retained_table_counts(source: Any, target: Any) -> dict[str, int]:
    """Compare every retained table by count and deterministic row-content digest."""

    source_tables = set(fetch_public_tables(source))
    source_retained = source_tables & set(PRE51_REQUIRED_TABLES)
    expected = expected_rebase_target_tables(source_tables)
    target_tables = set(fetch_public_tables(target)) - {MIGRATION_LEDGER_TABLE}
    if expected != target_tables:
        missing = sorted(expected - target_tables)
        extra = sorted(target_tables - expected)
        raise SchemaValidationError(
            f"目标表集合不一致：missing={missing}, extra={extra}"
        )
    counts: dict[str, int] = {}
    for table in sorted(source_retained):
        statement = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        source_count = int(source.execute(statement).fetchone()[0])
        target_count = int(target.execute(statement).fetchone()[0])
        if source_count != target_count:
            raise SchemaValidationError(
                f"表 {table} 行数不一致：source={source_count}, target={target_count}"
            )
        # 迁移可能合法删除保留表的列（如 mining_runs 的本体遗留列）：
        # 内容校验只比对源/目标共享列，被删列不参与指纹；
        # 被 001 改写行内容的表（mining_runs）豁免内容指纹，仅行数比对。
        if table in CONTENT_DIGEST_EXEMPT_TABLES:
            counts[table] = target_count
            continue
        shared = _shared_columns(source, target, table)
        source_digest = _table_digest(source, table, shared)
        target_digest = _table_digest(target, table, shared)
        if source_digest != target_digest:
            raise SchemaValidationError(
                f"表 {table} 内容校验和不一致（行数相同但数据发生变化）"
            )
        counts[table] = target_count
    for table in sorted(set(BRIDGE_51_TABLES) - source_retained):
        statement = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        counts[table] = int(target.execute(statement).fetchone()[0])
    return counts


def _shared_columns(source: Any, target: Any, table: str) -> tuple[str, ...]:
    """Columns present on both sides, in the target's physical order."""

    query = (
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "ORDER BY ordinal_position"
    )
    source_columns = {
        str(row[0]) for row in source.execute(query, (table,)).fetchall()
    }
    target_columns = [
        str(row[0]) for row in target.execute(query, (table,)).fetchall()
    ]
    shared = tuple(name for name in target_columns if name in source_columns)
    if not shared:
        raise SchemaValidationError(f"表 {table} 在源/目标没有共享列")
    return shared


def _table_digest(connection: Any, table: str, columns: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    projection = sql.SQL(",").join(sql.Identifier(name) for name in columns)
    statement = sql.SQL(
        "SELECT to_jsonb(row_data)::text FROM (SELECT {} FROM {}) AS row_data "
        "ORDER BY to_jsonb(row_data)::text"
    ).format(projection, sql.Identifier(table))
    with connection.cursor() as cursor:
        cursor.execute(statement)
        while True:
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            for row in rows:
                digest.update(str(row[0]).encode("utf-8"))
                digest.update(b"\n")
    return digest.hexdigest()


def fetch_public_tables(connection: Any) -> tuple[str, ...]:
    rows = connection.execute(
        """SELECT tablename FROM pg_catalog.pg_tables
             WHERE schemaname = 'public' ORDER BY tablename"""
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def validate_supported_rebase_source(connection: Any) -> tuple[str, ...]:
    """Accept only the complete immediate pre-51/51 schema family.

    Retired and legacy compatibility tables may still be present because the
    migration owns their removal.  Any other table means the source is outside
    the tested one-hop contract and must be assessed before a target is cloned.
    """

    tables = fetch_public_tables(connection)
    table_set = set(tables)
    missing = sorted(PRE51_REQUIRED_TABLES - table_set)
    if missing:
        raise SchemaValidationError(
            "源库不属于受支持的 pre-51 基线，缺少表：" + ", ".join(missing)
        )
    allowed = (
        set(FORMAL_TABLES)
        | set(RETIRED_TABLES)
        | set(LEGACY_COMPAT_TABLES)
        | {MIGRATION_LEDGER_TABLE}
    )
    unknown = sorted(table_set - allowed)
    if unknown:
        raise SchemaValidationError(
            "源库存在52迁移未声明的未知表：" + ", ".join(unknown)
        )
    return tables


def validate_schema(
    connection: Any,
    manifest: MigrationManifest,
    *,
    enforce_exact_count: bool = True,
    require_completion_marker: bool = True,
) -> SchemaValidationReport:
    applied = load_applied(connection)
    pending = pending_migrations(manifest, applied)
    if pending:
        raise SchemaValidationError(
            "数据库仍有未执行迁移：" + ", ".join(item.migration_id for item in pending)
        )
    if require_completion_marker and not schema_complete(
        connection, checksum=manifest_checksum(manifest)
    ):
        raise SchemaValidationError("最终 schema 签收标记缺失或校验码不匹配")
    tables = fetch_public_tables(connection)
    retired_present = sorted(
        set(tables) & (set(RETIRED_TABLES) | set(LEGACY_COMPAT_TABLES))
    )
    if retired_present:
        raise SchemaValidationError("退役表仍存在：" + ", ".join(retired_present))
    if MIGRATION_LEDGER_TABLE not in tables:
        raise SchemaValidationError("迁移账本不存在")
    formal_count = len(tables) - 1
    if enforce_exact_count and (
        formal_count != EXPECTED_FORMAL_TABLES or len(tables) != EXPECTED_PHYSICAL_TABLES
    ):
        raise SchemaValidationError(
            f"表数量不符：正式={formal_count}/{EXPECTED_FORMAL_TABLES}, "
            f"物理={len(tables)}/{EXPECTED_PHYSICAL_TABLES}"
        )
    extensions = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm', 'vector') ORDER BY extname"
        ).fetchall()
    )
    if extensions != ("pg_trgm", "vector"):
        raise SchemaValidationError("缺少 pg_trgm/vector 扩展")
    index_rows = connection.execute(
        """SELECT indexname, indexdef FROM pg_indexes
             WHERE schemaname = 'public' AND indexname = ANY(%s)""",
        (list(REQUIRED_INDEXES),),
    ).fetchall()
    index_defs = {str(row[0]): " ".join(str(row[1]).lower().split()) for row in index_rows}
    for name, (unique, tokens) in REQUIRED_INDEXES.items():
        definition = index_defs.get(name)
        if definition is None:
            raise SchemaValidationError(f"缺少关键索引：{name}")
        if unique and "create unique index" not in definition:
            raise SchemaValidationError(f"关键索引不是 UNIQUE：{name}")
        if any(token not in definition for token in tokens):
            raise SchemaValidationError(f"关键索引定义漂移：{name}: {definition}")
    return SchemaValidationReport(tables, extensions, manifest.schema_version)


__all__ = [
    "SchemaValidationError",
    "SchemaValidationReport",
    "compare_retained_table_counts",
    "fetch_public_tables",
    "expected_rebase_target_tables",
    "validate_schema",
    "validate_supported_rebase_source",
]
