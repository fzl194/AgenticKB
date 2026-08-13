"""Contract tests for the WP0.4 object storage foundation DDL (ADR-0003 D-004).

这些测试用 SQLite 在内存里执行 ``001_asset_core.sqlite.sql`` +
``008_object_storage_foundation.sql``，断言：

1. 新表存在（asset_storage_objects / asset_upload_sessions / asset_storage_object_refs /
   asset_file_audit_events / asset_storage_quotas / asset_storage_operations）。
2. 扩展列存在（asset_documents / asset_document_snapshots / asset_document_snapshot_links）。
3. 关键 UNIQUE/CHECK 约束生效：
   - storage_object 位置唯一（含 nullable object_version_id 的 COALESCE 归一）。
   - upload session 幂等键唯一。
   - artifact_class / state 枚举 CHECK。
   - snapshot_fingerprint partial unique。

只覆盖 SQLite 契约（D-004 增量、幂等）。PostgreSQL 版本（008_..._postgresql.sql）
语法由人工 + 002/003/004 的 DO 块风格保证；这里不连真实 PG。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from knowledge_mining.mining.infra.pg_schema import _split_ddl

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _REPO_ROOT / "databases" / "asset_core" / "schemas"

_BASE_SQLITE = _SCHEMA_DIR / "001_asset_core.sqlite.sql"
_WP0_4_SQLITE = _SCHEMA_DIR / "008_object_storage_foundation.sql"

# SQLite 不支持 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`（PG 扩展）。
# DDL 文件用 IF NOT EXISTS 表达幂等意图；在 SQLite 上加载时，把每条
# `ADD COLUMN IF NOT EXISTS` 降级为「检查列已存在则跳过，否则 ADD COLUMN」。
_ADD_COLUMN_RE = re.compile(
    r"^ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_INDEX_RE = re.compile(
    r"^CREATE\s+(?:UNIQUE\s+)?INDEX\b",
    re.IGNORECASE,
)


def _strip_comments(ddl: str) -> str:
    """Strip ``--`` line comments (leading and trailing) for statement splitting.

    Comments are documentation-only and irrelevant to execution; removing them
    avoids the trailing-comment-then-``;`` edge case in ``_split_ddl`` (which
    only treats a ``--`` as a comment start when it begins a line). String
    literals are respected so a ``--`` inside a quoted value is preserved.
    """
    out_lines: list[str] = []
    for line in ddl.splitlines():
        in_quote = False
        idx = 0
        while idx < len(line):
            ch = line[idx]
            if ch == "'":
                in_quote = not in_quote
            elif not in_quote and ch == "-" and idx + 1 < len(line) and line[idx + 1] == "-":
                break
            idx += 1
        out_lines.append(line[:idx].rstrip() if idx < len(line) else line)
    return "\n".join(out_lines)


def _load_schema() -> sqlite3.Connection:
    """Execute 001 + 008 against a fresh in-memory SQLite DB.

    SQLite lacks ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` (a PG extension),
    so the 008 file's ``ADD COLUMN IF NOT EXISTS`` statements are stripped of the
    ``IF NOT EXISTS`` clause and replayed with a ``PRAGMA table_info`` existence
    guard. ``CREATE INDEX`` statements that reference added columns are deferred
    to run after the columns exist.

    The 008 partial index ``idx_asset_documents_deleted_at`` references
    ``kb_id``, which the SQLite baseline (001) does not include — it is added by
    the PG-only ``004_kb_isolation.sql``. To reproduce PG's migration ordering
    (004 before 008), we add a minimal ``kb_id`` column to ``asset_documents``
    here before running 008.
    """
    connection = sqlite3.connect(":memory:")
    connection.executescript(_BASE_SQLITE.read_text(encoding="utf-8"))
    # Reproduce 004_kb_isolation's column-level additions that 008 depends on.
    _ensure_columns(connection, "asset_documents", {
        "kb_id": "TEXT",
        "storage_path": "TEXT",
        "directory_path": "TEXT",
    })

    raw_008 = _strip_comments(_WP0_4_SQLITE.read_text(encoding="utf-8"))
    statements = [s for s in _split_ddl(raw_008) if s.strip()]

    create_tables: list[str] = []
    add_columns: list[str] = []
    create_indexes: list[str] = []
    for stmt in statements:
        if re.match(r"CREATE\s+(?:UNIQUE\s+)?INDEX\b", stmt, re.IGNORECASE):
            create_indexes.append(stmt)
        elif stmt.lstrip().upper().startswith("ALTER"):
            add_columns.append(stmt)
        else:
            create_tables.append(stmt)

    # 1. CREATE TABLE / other DDL
    for stmt in create_tables:
        connection.execute(stmt)

    # 2. ADD COLUMN (existence-guarded, IF NOT EXISTS stripped for SQLite)
    for stmt in add_columns:
        match = _ADD_COLUMN_RE.match(stmt.lstrip())
        if not match:
            connection.execute(stmt)
            continue
        table, column, rest = match.group(1), match.group(2), match.group(3).strip()
        _ensure_columns(connection, table, {column: rest})

    # 3. CREATE INDEX (after columns exist)
    for stmt in create_indexes:
        connection.execute(stmt)

    return connection


def _ensure_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Add columns to ``table`` if missing (idempotent)."""
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    for column, coldef in columns.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _index_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


# ── 表存在性 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "table",
    [
        "asset_storage_objects",
        "asset_upload_sessions",
        "asset_storage_object_refs",
        "asset_file_audit_events",
        "asset_storage_quotas",
        "asset_storage_operations",
    ],
)
def test_new_tables_exist(table: str) -> None:
    with _load_schema() as connection:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    assert row is not None, f"table {table} missing after 008"


# ── 扩展列存在性 ─────────────────────────────────────────────────────────────

def test_asset_documents_extension_columns() -> None:
    with _load_schema() as connection:
        cols = _columns(connection, "asset_documents")
    expected = {
        "folder_id",
        "storage_object_id",
        "source_raw_hash",
        "content_revision",
        "content_updated_at",
        "deleted_at",
        "restored_at",
    }
    missing = expected - cols
    assert not missing, f"asset_documents missing columns: {missing}"
    # legacy 列保留（D-004：不删列）
    assert "storage_path" in cols, "storage_path legacy column must be preserved"


def test_asset_document_snapshots_extension_columns() -> None:
    with _load_schema() as connection:
        cols = _columns(connection, "asset_document_snapshots")
    expected = {
        "snapshot_fingerprint",
        "parse_ir_storage_object_id",
        "parse_ir_schema_version",
        "parser_fingerprint",
        "compiler_fingerprint",
        "quality_status",
        "lifecycle_status",
        "created_by_run_id",
    }
    missing = expected - cols
    assert not missing, f"asset_document_snapshots missing columns: {missing}"
    # 现有列保留（来自 001 baseline）
    assert "normalized_content_hash" in cols
    assert "raw_content_hash" in cols


def test_asset_document_snapshot_links_extension_columns() -> None:
    with _load_schema() as connection:
        cols = _columns(connection, "asset_document_snapshot_links")
    expected = {"source_storage_object_id", "source_content_revision"}
    missing = expected - cols
    assert not missing, f"asset_document_snapshot_links missing columns: {missing}"
    assert "source_uri" in cols, "source_uri legacy column must be preserved"


# ── storage_object 位置唯一（COALESCE NULL 归一） ────────────────────────────

def _insert_storage_object(
    connection: sqlite3.Connection,
    *,
    id_: str,
    provider: str = "minio",
    bucket: str = "b",
    object_key: str = "k",
    object_version_id: str | None,
    sha256: str = "h",
    size: int = 1,
    artifact_class: str = "source",
    state: str = "STAGING",
) -> None:
    connection.execute(
        """INSERT INTO asset_storage_objects
           (id, provider, bucket, object_key, object_version_id,
            sha256, size, artifact_class, state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (id_, provider, bucket, object_key, object_version_id,
         sha256, size, artifact_class, state),
    )


def test_storage_object_unique_with_explicit_version() -> None:
    with _load_schema() as connection:
        _insert_storage_object(connection, id_="o1", object_version_id="v1")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_storage_object(connection, id_="o2", object_version_id="v1")


def test_storage_object_unique_normalizes_null_version() -> None:
    """两个 object_version_id=NULL、相同 (provider,bucket,object_key) 应判重。

    SQLite 列级 UNIQUE(provider,bucket,object_key,object_version_id) 会放过两个 NULL，
    但表达式索引 COALESCE(object_version_id,'') 把 NULL 归一为 ''，因此应冲突。
    """
    with _load_schema() as connection:
        _insert_storage_object(connection, id_="cur1", object_version_id=None)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_storage_object(connection, id_="cur2", object_version_id=None)


def test_storage_object_distinct_keys_coexist() -> None:
    with _load_schema() as connection:
        _insert_storage_object(connection, id_="a", object_key="k1", object_version_id=None)
        _insert_storage_object(connection, id_="b", object_key="k2", object_version_id=None)
        # 不同 key + 不同 version 不冲突
        _insert_storage_object(connection, id_="c", object_key="k1", object_version_id="v2")


def test_storage_object_null_vs_version_distinguished() -> None:
    """NULL（COALESCE=''）与显式 'v1' 不应冲突。"""
    with _load_schema() as connection:
        _insert_storage_object(connection, id_="n", object_version_id=None)
        _insert_storage_object(connection, id_="v", object_version_id="v1")


# ── 枚举 CHECK ───────────────────────────────────────────────────────────────

def test_storage_object_artifact_class_check() -> None:
    with _load_schema() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_storage_object(connection, id_="bad", artifact_class="not_a_class", object_version_id=None)


def test_storage_object_state_check() -> None:
    with _load_schema() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_storage_object(connection, id_="bad", state="BOGUS", object_version_id=None)


def test_upload_session_state_check() -> None:
    with _load_schema() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO asset_upload_sessions
                   (id, kb_id, actor, original_filename, staging_object_key,
                    idempotency_key, expires_at, state)
                   VALUES ('s1','kb','u','f.xlsx','stg/k','ik','2099-01-01','NOPE')"""
            )


# ── upload session 幂等键唯一 ────────────────────────────────────────────────

def test_upload_session_idempotency_unique() -> None:
    with _load_schema() as connection:
        connection.execute(
            """INSERT INTO asset_upload_sessions
               (id, kb_id, actor, original_filename, staging_object_key,
                idempotency_key, expires_at)
               VALUES ('s1','kb','u','f.xlsx','stg/k','ik','2099-01-01')"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO asset_upload_sessions
                   (id, kb_id, actor, original_filename, staging_object_key,
                    idempotency_key, expires_at)
                   VALUES ('s2','kb','u','g.pdf','stg/k2','ik','2099-01-01')"""
            )


def test_upload_session_idempotency_scoped_to_kb_actor() -> None:
    """不同 kb_id 或 actor 下相同 idempotency_key 应允许共存。"""
    with _load_schema() as connection:
        connection.execute(
            """INSERT INTO asset_upload_sessions
               (id, kb_id, actor, original_filename, staging_object_key,
                idempotency_key, expires_at)
               VALUES ('s1','kb1','u','f','stg/k','ik','2099-01-01')"""
        )
        connection.execute(
            """INSERT INTO asset_upload_sessions
               (id, kb_id, actor, original_filename, staging_object_key,
                idempotency_key, expires_at)
               VALUES ('s2','kb2','u','f','stg/k','ik','2099-01-01')"""
        )
        connection.execute(
            """INSERT INTO asset_upload_sessions
               (id, kb_id, actor, original_filename, staging_object_key,
                idempotency_key, expires_at)
               VALUES ('s3','kb1','u2','f','stg/k','ik','2099-01-01')"""
        )


# ── snapshot_fingerprint partial unique ──────────────────────────────────────

def _insert_snapshot(
    connection: sqlite3.Connection,
    *,
    id_: str,
    domain: str = "d",
    snapshot_fingerprint: str | None,
    raw_content_hash: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO asset_document_snapshots
           (id, domain, normalized_content_hash, raw_content_hash, mime_type,
            snapshot_fingerprint, created_at)
           VALUES (?, ?, ?, ?, 'text/plain', ?, '2024-01-01T00:00:00Z')""",
        (id_, domain, f"norm-{id_}", raw_content_hash or f"raw-{id_}",
         snapshot_fingerprint),
    )


def test_snapshot_fingerprint_partial_unique_only_when_set() -> None:
    """指纹非 NULL 时 (domain, snapshot_fingerprint) 唯一；NULL 行互不冲突。"""
    with _load_schema() as connection:
        # NULL 指纹的存量行可以多条（M0 不阻塞）
        _insert_snapshot(connection, id_="s1", snapshot_fingerprint=None)
        _insert_snapshot(connection, id_="s2", snapshot_fingerprint=None)
        # 非空指纹正常插入
        _insert_snapshot(connection, id_="s3", snapshot_fingerprint="fp1")
        # 同 domain 同指纹冲突
        with pytest.raises(sqlite3.IntegrityError):
            _insert_snapshot(connection, id_="s4", snapshot_fingerprint="fp1")


def test_snapshot_fingerprint_unique_scoped_to_domain() -> None:
    """不同 domain 下相同指纹允许共存。"""
    with _load_schema() as connection:
        _insert_snapshot(connection, id_="s1", domain="d1", snapshot_fingerprint="fp")
        _insert_snapshot(connection, id_="s2", domain="d2", snapshot_fingerprint="fp")


# ── quota 乐观锁默认值 ───────────────────────────────────────────────────────

def test_storage_quota_defaults() -> None:
    with _load_schema() as connection:
        connection.execute(
            "INSERT INTO asset_storage_quotas (kb_id, limit_bytes) VALUES ('kb', 1000)"
        )
        row = connection.execute(
            "SELECT reserved_bytes, used_bytes, version FROM asset_storage_quotas WHERE kb_id='kb'"
        ).fetchone()
    assert row == (0, 0, 1)


def test_storage_quota_kb_unique() -> None:
    with _load_schema() as connection:
        connection.execute(
            "INSERT INTO asset_storage_quotas (kb_id, limit_bytes) VALUES ('kb', 1000)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO asset_storage_quotas (kb_id, limit_bytes) VALUES ('kb', 2000)"
            )


# ── 索引存在性 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "index_name",
    [
        "uq_asset_storage_objects_location",
        "uq_asset_upload_sessions_idem",
        "uq_asset_snapshot_fingerprint",
    ],
)
def test_key_indexes_exist(index_name: str) -> None:
    with _load_schema() as connection:
        assert _index_exists(connection, index_name), f"index {index_name} missing"


# =============================================================================
# PostgreSQL DDL — static validation & PG/SQLite parity
#
# 不连真实 PG（环境无 PG）。这些测试对 008_..._postgresql.sql 做结构化静态校验：
#   - 括号 / DO $$ 块平衡
#   - COALESCE(object_version_id,'') 表达式唯一索引语法正确（SRS §8.5 末段）
#   - snapshot_fingerprint partial unique 由「重复行检查」DO 块守卫（004 风格）
#   - PG 与 SQLite 版本的表 / 扩展列集合完全一致（D-003 双版本对齐）
# =============================================================================

_PG_DDL = _SCHEMA_DIR / "008_object_storage_foundation_postgresql.sql"


def _strip_sql_comments(ddl: str) -> str:
    out_lines = []
    for line in ddl.splitlines():
        in_quote = False
        idx = 0
        while idx < len(line):
            ch = line[idx]
            if ch == "'":
                in_quote = not in_quote
            elif not in_quote and ch == "-" and idx + 1 < len(line) and line[idx + 1] == "-":
                break
            idx += 1
        out_lines.append(line[:idx])
    return "\n".join(out_lines)


def test_pg_ddl_parentheses_balanced() -> None:
    code = _strip_sql_comments(_PG_DDL.read_text(encoding="utf-8"))
    # Ignore DO $$ ... $$ bodies for paren counting (they are PL/pgSQL).
    no_dollar = re.sub(r"\$\$.*?\$\$", "", code, flags=re.DOTALL)
    depth = 0
    for ch in no_dollar:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        assert depth >= 0, "unbalanced closing parenthesis in PG DDL"
    assert depth == 0, f"PG DDL has unbalanced parentheses (final depth {depth})"


def test_pg_ddl_object_version_coalesce_unique_index() -> None:
    """nullable object_version_id 经 COALESCE('',) 归一（SRS §8.5 末段）。"""
    code = _strip_sql_comments(_PG_DDL.read_text(encoding="utf-8"))
    pattern = (
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_storage_objects_location\s+"
        r"ON asset_storage_objects\([^)]*COALESCE\(object_version_id,\s*''\)"
    )
    assert re.search(pattern, code, re.IGNORECASE | re.DOTALL), (
        "COALESCE(object_version_id, '') expression unique index missing or malformed"
    )


def test_pg_ddl_snapshot_fingerprint_guarded_partial_unique() -> None:
    """snapshot_fingerprint partial unique 由重复行检查守卫（沿用 004 snapshot binding 风格）。"""
    code = _PG_DDL.read_text(encoding="utf-8")
    assert "uq_asset_snapshot_fingerprint" in code
    assert "GROUP BY domain, snapshot_fingerprint HAVING COUNT(*) > 1" in code
    assert "WHERE snapshot_fingerprint IS NOT NULL" in code


def test_pg_sqlite_table_parity() -> None:
    pg = _strip_sql_comments(_PG_DDL.read_text(encoding="utf-8"))
    sqlite_ddl = _WP0_4_SQLITE.read_text(encoding="utf-8")
    pg_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", pg))
    sqlite_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sqlite_ddl))
    assert pg_tables == sqlite_tables, (
        f"table parity broken: PG-only={pg_tables - sqlite_tables}, "
        f"SQLite-only={sqlite_tables - pg_tables}"
    )


@pytest.mark.parametrize(
    "table",
    ["asset_documents", "asset_document_snapshots", "asset_document_snapshot_links"],
)
def test_pg_sqlite_extension_column_parity(table: str) -> None:
    pg = _strip_sql_comments(_PG_DDL.read_text(encoding="utf-8"))
    sqlite_ddl = _WP0_4_SQLITE.read_text(encoding="utf-8")

    def added_cols(ddl_text: str) -> set[str]:
        return set(re.findall(
            rf"ALTER TABLE {table}\s+ADD COLUMN IF NOT EXISTS\s+(\w+)",
            ddl_text, re.IGNORECASE,
        ))

    pg_cols = added_cols(pg)
    sqlite_cols = added_cols(sqlite_ddl)
    assert pg_cols == sqlite_cols, (
        f"{table} extension column parity broken: "
        f"PG-only={pg_cols - sqlite_cols}, SQLite-only={sqlite_cols - pg_cols}"
    )
