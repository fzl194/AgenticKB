"""Transactional migration runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .ledger import (
    acquire_lock,
    ensure_ledger,
    load_applied,
    record_migration,
    record_schema_complete,
    release_lock,
)
from .manifest import MigrationManifest, manifest_checksum, pending_migrations


@dataclass(frozen=True, slots=True)
class MigrationRunResult:
    applied_ids: tuple[str, ...]
    schema_version: str


def apply_manifest(
    connection: Any,
    manifest: MigrationManifest,
    *,
    app_version: str,
    details: dict[str, object] | None = None,
    validate_before_complete: Callable[[], None],
    use_lock: bool = True,
) -> MigrationRunResult:
    """Apply each missing file and its ledger row in the same transaction."""

    if use_lock:
        acquire_lock(connection)
    try:
        with connection.transaction():
            ensure_ledger(connection)
        applied = load_applied(connection)
        pending = pending_migrations(manifest, applied)
        applied_ids: list[str] = []
        for migration in pending:
            sql_text = migration.path.read_text(encoding="utf-8")
            with connection.transaction():
                connection.execute(sql_text)
                record_migration(
                    connection,
                    migration_id=migration.migration_id,
                    checksum=migration.checksum,
                    app_version=app_version,
                    details={
                        **(details or {}),
                        "migration_id": migration.migration_id,
                        "migration_mode": migration.mode.value,
                    },
                )
            applied_ids.append(migration.migration_id)
        # The release marker is the startup gate. It must be written only after
        # the caller's full structural/data validation succeeds.
        validate_before_complete()
        with connection.transaction():
            record_schema_complete(
                connection,
                checksum=manifest_checksum(manifest),
                app_version=app_version,
                details={"schema_version": manifest.schema_version, **(details or {})},
            )
        return MigrationRunResult(tuple(applied_ids), manifest.schema_version)
    finally:
        if use_lock:
            release_lock(connection)


__all__ = ["MigrationRunResult", "apply_manifest"]
