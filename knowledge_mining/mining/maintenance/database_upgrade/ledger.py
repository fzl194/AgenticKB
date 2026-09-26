"""PostgreSQL migration ledger and advisory-lock helpers."""

from __future__ import annotations

from typing import Any, Mapping

from psycopg.types.json import Jsonb

from .contract import MIGRATION_LEDGER_TABLE, SCHEMA_MARKER_ID
from .manifest import MigrationManifest, MigrationMode, manifest_checksum


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


class LedgerRollbackError(RuntimeError):
    """Ledger rows are not safe to remove for an expand-only code rollback."""


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


def rollback_online_migration_records(
    connection: Any,
    manifest: MigrationManifest,
    *,
    app_version: str,
    deployment_id: str,
    schema_marker_id: str = SCHEMA_MARKER_ID,
) -> tuple[str, ...]:
    """Remove only this release's online ledger rows; compatible DDL stays in place."""

    if not ledger_exists(connection):
        return ()
    deployment = str(deployment_id).strip()
    if not deployment:
        raise LedgerRollbackError("在线迁移回滚缺少 deployment_id")
    rollback_candidates = {
        item.migration_id: item
        for item in manifest.migrations
        if item.mode in (MigrationMode.ONLINE_EXPAND, MigrationMode.BACKFILL)
    }
    candidate_ids = [*rollback_candidates, schema_marker_id]
    with connection.transaction():
        rows = connection.execute(
            f"""SELECT migration_id, checksum, app_version, details_json
                  FROM {MIGRATION_LEDGER_TABLE}
                 WHERE migration_id = ANY(%s)""",
            (candidate_ids,),
        ).fetchall()
        present: set[str] = set()
        expected_manifest_checksum = manifest_checksum(manifest)
        for raw_id, raw_checksum, raw_app_version, raw_details in rows:
            migration_id = str(raw_id)
            details = raw_details if isinstance(raw_details, Mapping) else {}
            is_current_deployment = (
                str(raw_app_version) == app_version
                and details.get("deployment_id") == deployment
            )
            if not is_current_deployment:
                continue
            if migration_id == schema_marker_id:
                valid = (
                    str(raw_checksum) == expected_manifest_checksum
                    and details.get("mode") == "online_migrate"
                    and details.get("schema_version") == manifest.schema_version
                )
            else:
                migration = rollback_candidates.get(migration_id)
                if (
                    migration is not None
                    and migration.mode is MigrationMode.BACKFILL
                    and not migration.reentrant
                ):
                    raise LedgerRollbackError(
                        f"backfill {migration_id} 未声明 reentrant=true，不可自动回滚"
                    )
                valid = bool(
                    migration
                    and str(raw_checksum) == migration.checksum
                    and details.get("mode") == "online_migrate"
                    and details.get("migration_mode") == migration.mode.value
                )
            if not valid:
                raise LedgerRollbackError(
                    f"迁移账本 {migration_id} 不属于本次在线迁移，拒绝删除"
                )
            present.add(migration_id)
        ordered = tuple(item for item in candidate_ids if item in present)
        if ordered:
            connection.execute(
                f"DELETE FROM {MIGRATION_LEDGER_TABLE} WHERE migration_id = ANY(%s)",
                (list(ordered),),
            )
    return ordered


__all__ = [
    "LEDGER_DDL",
    "LedgerRollbackError",
    "MIGRATION_LOCK_KEY",
    "MigrationLockUnavailable",
    "acquire_lock",
    "ensure_ledger",
    "ledger_exists",
    "load_applied",
    "record_migration",
    "record_schema_complete",
    "rollback_online_migration_records",
    "release_lock",
    "schema_complete",
]
