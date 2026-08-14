"""PostgreSQL schema initialization for Mining v3.0.

Ensures the target database exists (creates if needed),
then applies DDL for both asset_core and mining_runtime tables.
"""
from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from .pg_config import MiningDbConfig

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ASSET_DDL = _REPO_ROOT / "databases" / "asset_core" / "schemas" / "002_asset_core_postgresql.sql"
_RUNTIME_DDL = _REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "002_mining_runtime_postgresql.sql"
_RUNTIME_DDL_V3 = _REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "003_mining_runtime_domain.sql"
_RUNTIME_DDL_V4 = _REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "004_mining_runtime_run_stage.sql"
_RUNTIME_DDL_V5 = _REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "005_mining_workflow_runtime.sql"
_RUNTIME_DDL_V6 = _REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "006_mining_run_preflight.sql"
_ASSET_DOMAIN_DDL = _REPO_ROOT / "databases" / "asset_core" / "schemas" / "003_asset_core_domain_isolation.sql"
_ASSET_WORKFLOW_DDL = _REPO_ROOT / "databases" / "asset_core" / "schemas" / "004_asset_snapshot_workflow_binding.sql"
# Ontology concept layer — must apply AFTER asset/runtime (FKs target asset_* and mining_runs).
_ONTOLOGY_DDL = _REPO_ROOT / "databases" / "ontology" / "schemas" / "001_ontology_concept_postgresql.sql"
# KB management — kb 三表 + asset_documents 004 ALTER。
# 004_kb_isolation 引用 knowledge_bases（FK），必须在 kb 三表之后；且引用 asset_documents，
# 必须在 002_asset_core 之后。运行时按序：asset_core → kb 三表 → kb_isolation → runtime → ... → ontology。
_KB_USERS_DDL = _REPO_ROOT / "databases" / "kb" / "schemas" / "001_kb_users.sql"
# Phase 2 鉴权：ALTER kb_users 加 password_hash + site_role。必须紧跟 001（ALTER 依赖基表已建）。
_KB_USERS_AUTH_DDL = _REPO_ROOT / "databases" / "kb" / "schemas" / "006_kb_users_auth.sql"
_KB_BASES_DDL = _REPO_ROOT / "databases" / "kb" / "schemas" / "002_knowledge_bases.sql"
# 收口 visibility 为 private/public(ALTER knowledge_bases 的 CHECK,必须紧跟 002 之后)。
_KB_VISIBILITY_DDL = _REPO_ROOT / "databases" / "kb" / "schemas" / "007_kb_visibility_narrow.sql"
_KB_MEMBERS_DDL = _REPO_ROOT / "databases" / "kb" / "schemas" / "003_kb_members.sql"
_KB_FOLDERS_DDL = _REPO_ROOT / "databases" / "kb" / "schemas" / "004_kb_folders.sql"
_KB_ISOLATION_DDL = _REPO_ROOT / "databases" / "asset_core" / "schemas" / "004_kb_isolation.sql"
_KB_FILE_META_DDL = _REPO_ROOT / "databases" / "asset_core" / "schemas" / "005_kb_file_meta.sql"
# KB 中心化挖掘（P2'）：kb 范式绑定 + build/run 的 kb_id 归属列。
_KB_MINING_BINDING_DDL = _REPO_ROOT / "databases" / "kb" / "schemas" / "005_kb_mining_binding.sql"
_ASSET_BUILD_KB_DDL = _REPO_ROOT / "databases" / "asset_core" / "schemas" / "006_asset_build_kb.sql"
_ASSET_BLOCK_TYPE_IMAGE_DDL = _REPO_ROOT / "databases" / "asset_core" / "schemas" / "007_asset_block_type_image.sql"
_MINING_RUN_KB_DDL = _REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "007_mining_run_kb.sql"
# M1 对象存储地基（WP0.4/WP1A）：storage objects / upload sessions / quotas /
# outbox + asset_documents/snapshots/snapshot_links 扩展。纯增量幂等（ADR-0003 D-004）。
_OBJECT_STORAGE_DDL = (
    _REPO_ROOT / "databases" / "asset_core" / "schemas" / "008_object_storage_foundation_postgresql.sql"
)
# M2 影子解析运行投影（Shadow Parse）：asset_parse_runs 一张表，纯增量幂等。
# 依赖 008（parse_ir_storage_object_id 指向 asset_storage_objects），必须挂在其后。
_SHADOW_PARSE_DDL = (
    _REPO_ROOT / "databases" / "asset_core" / "schemas" / "009_shadow_parse_runs_postgresql.sql"
)
_WORKFLOW_CONTROL_DDL = _REPO_ROOT / "databases" / "mining_control" / "schemas" / "001_mining_workflow_postgresql.sql"


def _connect_safely(
    cfg: MiningDbConfig,
    *,
    maintenance: bool,
    autocommit: bool,
):
    """Open PostgreSQL without propagating credential-bearing driver errors."""
    database_name = "postgres" if maintenance else cfg.pg_dbname
    conninfo = cfg.maintenance_conninfo if maintenance else cfg.conninfo
    try:
        return psycopg.connect(
            conninfo,
            autocommit=autocommit,
            connect_timeout=5,
        )
    except psycopg.Error:
        # psycopg connection errors may echo the full conninfo (including the
        # password). Suppress the driver context at this boundary.
        raise RuntimeError(
            f"Unable to connect to PostgreSQL database {database_name!r}"
        ) from None


def ensure_database(cfg: MiningDbConfig) -> None:
    """Create the target database if it doesn't exist (connects to postgres maintenance DB)."""
    from psycopg import sql

    conn = _connect_safely(
        cfg,
        maintenance=True,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (cfg.pg_dbname,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cfg.pg_dbname)))
                logger.info("Created database %s", cfg.pg_dbname)
            else:
                logger.info("Database %s already exists", cfg.pg_dbname)
    finally:
        conn.close()


def domain_schema_paths() -> tuple[Path, ...]:
    """Return asset/runtime DDLs that are safe for every Domain database."""
    return (
        _ASSET_DDL,
        # KB management — kb 三表 + folders（FK→knowledge_bases）+ asset_documents ALTER。
        # kb_isolation 引用 knowledge_bases 与 asset_documents，必须在这两者之后。
        _KB_USERS_DDL,
        _KB_USERS_AUTH_DDL,  # Phase 2 鉴权列（ALTER kb_users，紧跟 001）
        _KB_BASES_DDL,
        _KB_VISIBILITY_DDL,  # 收口 visibility CHECK(private/public),紧跟 002
        _KB_MEMBERS_DDL,
        _KB_FOLDERS_DDL,
        _KB_ISOLATION_DDL,
        _KB_FILE_META_DDL,
        _RUNTIME_DDL,
        _RUNTIME_DDL_V3,
        _RUNTIME_DDL_V4,
        _RUNTIME_DDL_V5,
        _RUNTIME_DDL_V6,
        _ASSET_DOMAIN_DDL,
        _ASSET_WORKFLOW_DDL,
        _ONTOLOGY_DDL,
        # KB 中心化挖掘（P2'）：放最后，确保引用的基表（knowledge_bases / asset_builds / mining_runs）都已建。
        _KB_MINING_BINDING_DDL,
        _ASSET_BUILD_KB_DDL,
        _ASSET_BLOCK_TYPE_IMAGE_DDL,
        _MINING_RUN_KB_DDL,
        # M1 对象存储地基：依赖 asset_documents / asset_document_snapshots（链内已建）。
        _OBJECT_STORAGE_DDL,
        # M2 影子解析投影：依赖 008 的 asset_storage_objects（parse_ir 对象注册），挂链尾。
        _SHADOW_PARSE_DDL,
    )


def primary_schema_paths() -> tuple[Path, ...]:
    """Return primary DB DDLs, including the global Workflow control store."""
    return (*domain_schema_paths(), _WORKFLOW_CONTROL_DDL)


def _ensure_schema_paths(cfg: MiningDbConfig, ddl_paths: tuple[Path, ...]) -> None:
    ensure_database(cfg)

    conn = _connect_safely(
        cfg,
        maintenance=False,
        autocommit=True,
    )
    try:
        for ddl_path in ddl_paths:
            ddl = ddl_path.read_text(encoding="utf-8")
            _execute_ddl(
                conn,
                ddl,
                transactional=ddl_path
                in (
                    _RUNTIME_DDL_V4,
                    _RUNTIME_DDL_V5,
                    _RUNTIME_DDL_V6,
                    _ASSET_DOMAIN_DDL,
                    _ASSET_WORKFLOW_DDL,
                    _KB_ISOLATION_DDL,
                    _KB_MINING_BINDING_DDL,
                    _ASSET_BUILD_KB_DDL,
                    _MINING_RUN_KB_DDL,
                ),
            )
            logger.info("Applied DDL: %s", ddl_path.name)
    finally:
        conn.close()


def ensure_primary_schema(cfg: MiningDbConfig) -> None:
    """Initialize the primary database, including global Workflow control tables."""
    _ensure_schema_paths(cfg, primary_schema_paths())


def ensure_domain_schema(cfg: MiningDbConfig) -> None:
    """Initialize one Domain database without global Workflow control tables."""
    _ensure_schema_paths(cfg, domain_schema_paths())


def ensure_schema(cfg: MiningDbConfig) -> None:
    """Backward-compatible primary schema initializer."""
    ensure_primary_schema(cfg)


def _execute_ddl(conn, ddl: str, *, transactional: bool = False) -> None:
    """Execute DDL statement-by-statement, ignoring duplicate object errors.

    Splits on semicolons but respects dollar-quoted strings ($$...$$)
    used in PL/pgSQL function bodies.
    """
    import psycopg.errors

    stmts = _split_ddl(ddl)
    if transactional:
        with conn.transaction():
            for stmt in stmts:
                stmt = _strip_leading_comments(stmt)
                if not stmt:
                    continue
                try:
                    with conn.cursor() as cur:
                        cur.execute(stmt)
                except Exception as exc:
                    logger.error("DDL execution failed: %s | Statement: %s", exc, stmt[:200])
                    raise
        return

    for stmt in stmts:
        stmt = _strip_leading_comments(stmt)
        if not stmt:
            continue
        try:
            with conn.cursor() as cur:
                cur.execute(stmt)
        except (
            psycopg.errors.DuplicateObject,
            psycopg.errors.DuplicateTable,
            psycopg.errors.DuplicateFunction,
        ):
            pass  # Already exists — idempotent
        except Exception as exc:
            logger.error("DDL execution failed: %s | Statement: %s", exc, stmt[:200])
            raise


def _strip_leading_comments(stmt: str) -> str:
    """Remove leading single-line comments (-- ...) from a SQL statement."""
    lines = stmt.split("\n")
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            start = i + 1
        else:
            break
    result = "\n".join(lines[start:]).strip()
    return result


def _split_ddl(ddl: str) -> list[str]:
    """Split DDL on semicolons, respecting $$ quoting and line comments."""
    stmts: list[str] = []
    current: list[str] = []
    in_dollar_quote = False
    in_line_comment = False

    i = 0
    while i < len(ddl):
        if in_line_comment:
            current.append(ddl[i])
            if ddl[i] == "\n":
                in_line_comment = False
            i += 1
        elif ddl[i:i+2] == "--" and not in_dollar_quote:
            in_line_comment = True
            current.append("--")
            i += 2
        elif ddl[i:i+2] == "$$" and not in_dollar_quote:
            in_dollar_quote = True
            current.append("$$")
            i += 2
        elif ddl[i:i+2] == "$$" and in_dollar_quote:
            in_dollar_quote = False
            current.append("$$")
            i += 2
        elif ddl[i] == ";" and not in_dollar_quote:
            current.append(";")
            stmt = "".join(current).strip()
            if stmt:
                stmts.append(stmt)
            current = []
            i += 1
        else:
            current.append(ddl[i])
            i += 1

    # Handle remaining text
    remaining = "".join(current).strip()
    if remaining:
        stmts.append(remaining)

    return stmts
