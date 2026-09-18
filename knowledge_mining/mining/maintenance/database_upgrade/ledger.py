"""PostgreSQL migration ledger and advisory-lock helpers."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from .contract import MIGRATION_LEDGER_TABLE, SCHEMA_MARKER_ID


MIGRATION_LOCK_KEY = 0x434D4B425F4442  # "CMKB_DB"

LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS {MIGRATION_LEDGER_TABLE} (
    migration_id TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    app_version TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    details_json JSONB NOT NULL DEFAULT '{{}}'::jsonb
)
"""


class MigrationLockUnavailable(RuntimeError):
    """Another deployment owns the database migration lock."""


def ledger_exists(connection: Any) -> bool:
    row = connection.execute(
        "SELECT to_regclass(%s) IS NOT NULL", (f"public.{MIGRATION_LEDGER_TABLE}",)
    ).fetchone()
    return bool(row and row[0])


def ensure_ledger(connection: Any) -> None:
    connection.execute(LEDGER_DDL)


def acquire_lock(connection: Any) -> None:
    row = connection.execute(
        "SELECT pg_try_advisory_lock(%s)", (MIGRATION_LOCK_KEY,)
    ).fetchone()
    if not row or not row[0]:
        raise MigrationLockUnavailable("另一个数据库迁移正在执行")


def release_lock(connection: Any) -> None:
    connection.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


def load_applied(connection: Any) -> dict[str, str]:
    if not ledger_exists(connection):
        return {}
    rows = connection.execute(
        f"""SELECT migration_id, checksum FROM {MIGRATION_LEDGER_TABLE}
             WHERE migration_id NOT LIKE 'schema/%' ORDER BY applied_at"""
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def schema_complete(connection: Any, *, checksum: str) -> bool:
    if not ledger_exists(connection):
        return False
    row = connection.execute(
        f"""SELECT EXISTS (
                SELECT 1 FROM {MIGRATION_LEDGER_TABLE}
                 WHERE migration_id = %s AND checksum = %s
             )""",
        (SCHEMA_MARKER_ID, checksum),
    ).fetchone()
    return bool(row and row[0])


def record_migration(
    connection: Any,
    *,
    migration_id: str,
    checksum: str,
    app_version: str,
    details: dict[str, object],
) -> None:
    connection.execute(
        f"""INSERT INTO {MIGRATION_LEDGER_TABLE}
              (migration_id, checksum, app_version, details_json)
            VALUES (%s, %s, %s, %s)""",
        (migration_id, checksum, app_version, Jsonb(details)),
    )


def record_schema_complete(
    connection: Any,
    *,
    checksum: str,
    app_version: str,
    details: dict[str, object],
) -> None:
    connection.execute(
        f"""INSERT INTO {MIGRATION_LEDGER_TABLE}
              (migration_id, checksum, app_version, details_json)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (migration_id) DO UPDATE SET
              checksum = EXCLUDED.checksum,
              app_version = EXCLUDED.app_version,
              applied_at = now(),
              details_json = EXCLUDED.details_json""",
        (SCHEMA_MARKER_ID, checksum, app_version, Jsonb(details)),
    )


__all__ = [
    "LEDGER_DDL",
    "MIGRATION_LOCK_KEY",
    "MigrationLockUnavailable",
    "acquire_lock",
    "ensure_ledger",
    "ledger_exists",
    "load_applied",
    "record_migration",
    "record_schema_complete",
    "release_lock",
    "schema_complete",
]
