"""Legacy local-file migration package (M1.5, WP1C; ADR-0003 D-025).

This package implements SRS §8.8 Phase 2 (historical backfill): it migrates the
``asset_documents.storage_path`` legacy files into the object store + the new
``storage_object_id`` / ``raw_hash`` / ``content_revision`` columns on
``asset_documents`` — WITHOUT touching the existing DocumentService / jobs read
or write paths (ADR-0003 D-004).

The migration is a one-shot TOOL, not part of the hot request path. It depends
only on the M1.2 Repository Protocols (``contracts/file_management``) and the
M1.1 ``ObjectStorePort`` (``contracts/storage/port``); the PG repos, the
MinIO adapter, the in-memory fakes, and a filesystem-backed inventory are all
injected at construction so the full suite runs without PostgreSQL.

Public surface:
- Records: ``MigrationItem``, ``MigrationTaskResult``, ``MigrationReport``.
- Status constants: ``MigrationTaskStatus``.
- Protocols: ``MigrationInventory`` (the data source), ``MigrationProgressStore``
  (idempotent recovery ledger).
- Service: ``FileMigrationService`` (``service.py``).

References:
- SRS §8.8 (Phase 0-6, esp. Phase 2 + migration report fields).
- SRS §8.7 (replacement boundary — ``storage_path`` becomes a legacy alias).
- SRS §A23 (acceptance: idempotent rerun, verify-before-switch, MinIO-first).
- ADR-0003 D-004 (M0 only adds columns, migration is M1), D-005 (per-milestone
  commits), D-025 (this package: no read/write path changes).
"""
from __future__ import annotations

from knowledge_mining.mining.file_migration.contracts import (
    MigrationInventory,
    MigrationItem,
    MigrationProgressStore,
    MigrationReport,
    MigrationTaskResult,
    MigrationTaskStatus,
)
from knowledge_mining.mining.file_migration.service import (
    FileMigrationService,
    FileMigrationServiceConfig,
)

__all__ = [
    "FileMigrationService",
    "FileMigrationServiceConfig",
    "MigrationInventory",
    "MigrationItem",
    "MigrationProgressStore",
    "MigrationReport",
    "MigrationTaskResult",
    "MigrationTaskStatus",
]
