"""PostgreSQL schema initialization for llm_service.

Ensures the target database exists (creates if needed),
then applies DDL for agent_llm_runtime tables.
"""
from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from .pg_config import LlmDbConfig

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DDL_DIR = _REPO_ROOT / "databases" / "agent_llm_runtime" / "schemas"
# Ordered PostgreSQL DDL files (dictionary sort: 002, 003, ...). The 001 base
# file is the sqlite variant and is deliberately excluded — PostgreSQL applies
# the converted full schema in 002 plus incremental migrations after it.
_DDL_PATHS = sorted(_DDL_DIR.glob("*_postgresql.sql"))


def ensure_database(cfg: LlmDbConfig) -> None:
    """Create the target database if it doesn't exist (connects to postgres maintenance DB)."""
    from psycopg import sql

    conn = psycopg.connect(cfg.maintenance_conninfo, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (cfg.dbname,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cfg.dbname)))
                logger.info("Created database %s", cfg.dbname)
            else:
                logger.info("Database %s already exists", cfg.dbname)
    finally:
        conn.close()


def ensure_schema(cfg: LlmDbConfig) -> None:
    """Ensure database exists, then execute DDL file (idempotent).

    Before applying DDL, terminate stale "idle in transaction" connections
    left by previously killed processes to avoid lock contention.
    """
    ensure_database(cfg)

    # Terminate stale "idle in transaction" connections that may hold locks
    # from a previously SIGKILL'd process
    _cleanup_stale_connections(cfg)

    conn = psycopg.connect(cfg.conninfo, autocommit=True)
    try:
        for ddl_path in _DDL_PATHS:
            ddl = ddl_path.read_text(encoding="utf-8")
            _execute_ddl(conn, ddl)
            logger.info("Applied DDL: %s", ddl_path.name)
    finally:
        conn.close()


def _cleanup_stale_connections(cfg: LlmDbConfig) -> None:
    """Kill "idle in transaction" and long-running "active" connections to avoid lock waits."""
    conn = psycopg.connect(cfg.maintenance_conninfo, autocommit=True)
    try:
        with conn.cursor() as cur:
            # Terminate idle-in-transaction connections (these hold locks from crashed processes)
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND state = 'idle in transaction' AND pid <> pg_backend_pid()",
                (cfg.dbname,),
            )
            killed_idle = sum(1 for r in cur.fetchall() if r[0])
            if killed_idle:
                logger.warning("Terminated %d stale 'idle in transaction' connections", killed_idle)

            # Terminate long-running active queries on our DB (stuck > 30s)
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND state = 'active' AND query_start < now() - interval '30 seconds' "
                "AND pid <> pg_backend_pid()",
                (cfg.dbname,),
            )
            killed_active = sum(1 for r in cur.fetchall() if r[0])
            if killed_active:
                logger.warning("Terminated %d stuck active queries (>30s)", killed_active)
    except Exception:
        logger.exception("Failed to cleanup stale connections (non-fatal)")
    finally:
        conn.close()


def _execute_ddl(conn, ddl: str) -> None:
    """Execute DDL statement-by-statement, ignoring duplicate object errors."""
    import psycopg.errors

    stmts = _split_ddl(ddl)
    for stmt in stmts:
        # Strip leading/trailing comment lines — keep the actual SQL
        lines = stmt.strip().split('\n')
        sql_lines = [l for l in lines if not l.strip().startswith('--')]
        stmt = '\n'.join(sql_lines).strip()
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
            pass


def _split_ddl(ddl: str) -> list[str]:
    """Split DDL on semicolons, respecting $$ quoting."""
    stmts: list[str] = []
    current: list[str] = []
    in_dollar_quote = False

    i = 0
    while i < len(ddl):
        if ddl[i:i+2] == "$$" and not in_dollar_quote:
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

    remaining = "".join(current).strip()
    if remaining:
        stmts.append(remaining)

    return stmts
