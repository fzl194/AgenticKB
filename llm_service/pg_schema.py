"""Explicit LLM bootstrap helpers and production schema-contract validation.

Production startup is read-only.  ``ensure_schema`` remains available only to
explicit bootstrap/test fixtures; deployments run the versioned container
migration command before services start.
"""
from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from knowledge_mining.mining.maintenance.database_upgrade.contract import (
    CURRENT_SCHEMA_CHECKSUM,
    CURRENT_SCHEMA_VERSION,
    MIGRATION_LEDGER_TABLE,
    SCHEMA_MARKER_ID,
)

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
    """Explicitly bootstrap LLM tables for tests/new empty databases."""
    ensure_database(cfg)
    conn = psycopg.connect(cfg.conninfo, autocommit=True)
    try:
        for ddl_path in _DDL_PATHS:
            ddl = ddl_path.read_text(encoding="utf-8")
            _execute_ddl(conn, ddl)
            logger.info("Applied DDL: %s", ddl_path.name)
    finally:
        conn.close()


def assert_schema_contract(cfg: LlmDbConfig) -> None:
    """Fail closed unless the container migration reached this release version."""

    with psycopg.connect(cfg.conninfo, autocommit=True) as conn:
        ledger_row = conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL",
            (f"public.{MIGRATION_LEDGER_TABLE}",),
        ).fetchone()
        if not ledger_row or not ledger_row[0]:
            raise RuntimeError(
                "database migration required: migration ledger is missing; "
                "run deploy-sync.sh apply before starting llm_service"
            )
        version_row = conn.execute(
            f"""SELECT EXISTS (
                    SELECT 1 FROM {MIGRATION_LEDGER_TABLE}
                     WHERE migration_id = %s AND checksum = %s
                       AND details_json->>'schema_version' = %s
                 )""",
            (SCHEMA_MARKER_ID, CURRENT_SCHEMA_CHECKSUM, CURRENT_SCHEMA_VERSION),
        ).fetchone()
        if not version_row or not version_row[0]:
            raise RuntimeError(
                f"database migration required: expected schema {CURRENT_SCHEMA_VERSION}"
            )


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
