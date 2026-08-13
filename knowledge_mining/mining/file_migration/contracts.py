"""Contracts for the legacy local-file migration (M1.5, WP1C; ADR-0003 D-025).

Pure-stdlib frozen dataclasses + ``Protocol`` interfaces, mirroring the layering
used by ``contracts/file_management.py``. The migration service
(``file_migration.service.FileMigrationService``) depends ONLY on these
protocols + the M1.1 ``ObjectStorePort`` + the M1.2 ``StorageObjectRepository``
and ``DocumentCurrentContentRepository``; the PG implementation is injected at
composition time and the in-memory fakes drive the test suite without
PostgreSQL.

Design (SRS §8.8, §A23; ADR-0003 D-004, D-025):
- The migration is per-document idempotent and resumable: each document moves
  through ``PENDING -> UPLOADING -> VERIFIED -> SWITCHED`` (or ``FAILED`` with
  an ``error_reason``). A rerun / ``resume`` skips anything already ``SWITCHED``.
- Optimistic concurrency is honored: if the document's ``content_revision``
  moves under the migration (someone edited it mid-run), the migration task
  MUST fail with ``revision_conflict`` and NOT switch the pointer — the new
  content wins (SRS §8.8).
- The migration never sets the document current-content pointer before the
  object's ``size``/``sha256`` is verified against the store (SRS §A23).
- The migration does not read or modify the existing DocumentService / jobs
  paths (ADR-0003 D-004).

References:
- SRS §8.8 (Phase 2 + report fields).
- SRS §A23 (acceptance).
- ADR-0003 D-004 (M0 only adds columns), D-025 (this package's scope).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Status vocabulary (SRS §8.8: PENDING/UPLOADING/VERIFIED/SWITCHED/FAILED)
# ---------------------------------------------------------------------------


class MigrationTaskStatus:
    """Per-document migration lifecycle states (SRS §8.8).

    ``PENDING``    -> the inventory emitted the item but no attempt yet.
    ``UPLOADING``  -> the file has been opened and is being hashed / uploaded.
    ``VERIFIED``   -> the object is in the store and ``stat`` checks pass; the
                      StorageObject row is registered; the pointer is NOT yet
                      switched.
    ``SWITCHED``   -> the document current-content pointer has been advanced
                      (terminal-success — idempotent rerun skips this).
    ``FAILED``     -> terminal-failure with an ``error_reason``; reruns retry
                      unless the reason was a hard data error (e.g.
                      ``missing_file`` for a path that no longer exists).
    """

    PENDING = "PENDING"
    UPLOADING = "UPLOADING"
    VERIFIED = "VERIFIED"
    SWITCHED = "SWITCHED"
    FAILED = "FAILED"


# Error-reason vocabulary carried by ``MigrationTaskResult.error_reason``.
# These are machine-readable keys; human context goes in the report.
REASON_MISSING_FILE = "missing_file"
REASON_PERMISSION = "permission_failed"
REASON_HASH_CONFLICT = "hash_conflict"
REASON_REVISION_CONFLICT = "revision_conflict"
REASON_ORPHAN = "orphan_file"
REASON_UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Frozen records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MigrationItem:
    """One document to migrate, as emitted by :class:`MigrationInventory`.

    ``storage_path`` is the legacy local POSIX path of the source bytes.
    ``current_content_revision`` is the document's ``content_revision`` AT
    INVENTORY TIME — the migration passes it as ``expected_revision`` to
    ``set_current_content`` so any concurrent edit is detected (SRS §8.8
    optimistic concurrency).
    """

    document_id: str
    kb_id: str
    storage_path: str
    current_content_revision: int
    size_hint: int | None = None
    mime_hint: str | None = None


@dataclass(frozen=True)
class MigrationTaskResult:
    """Outcome of migrating a single document (SRS §8.8).

    On success (``status == SWITCHED``) ``storage_object_id``, ``sha256``,
    ``size`` and ``bytes_migrated`` are populated and ``error_reason`` is None.
    On failure (``status == FAILED``) ``error_reason`` carries one of the
    ``REASON_*`` constants and the other fields reflect partial progress
    (e.g. ``bytes_migrated`` may be non-zero if the upload succeeded but the
    pointer switch failed).
    """

    document_id: str
    status: str
    storage_object_id: str | None = None
    sha256: str | None = None
    size: int | None = None
    error_reason: str | None = None
    bytes_migrated: int = 0


@dataclass(frozen=True)
class MigrationReport:
    """Aggregate outcome of a migration run (SRS §8.8 report fields).

    Field names are aligned 1:1 with SRS §8.8 "迁移报告至少输出：总数、已迁移、
    缺失、hash 冲突、权限失败、孤儿文件、回退读取次数". ``per_document`` is the
    full ordered tuple of individual results for audit / drill-down.
    """

    total: int
    migrated: int  # documents that reached SWITCHED
    switched: int  # alias for migrated (kept for clarity with status name)
    failed: int
    missing_files: int
    hash_conflicts: int
    permission_failed: int
    orphan_files: int
    fallback_read_count: int
    duration_seconds: float
    per_document: tuple[MigrationTaskResult, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class MigrationInventory(Protocol):
    """Data-source abstraction for the set of documents to migrate.

    The filesystem implementation (``inventory_fs.FilesystemMigrationInventory``)
    reads a pre-built list of ``MigrationItem`` (pointing at real files under
    ``tmp_path`` in tests, or an on-disk inventory file in dev). The PG
    implementation (``inventory_pg``) runs
    ``SELECT ... WHERE storage_path IS NOT NULL AND storage_object_id IS NULL``
    and is PG-gated — it is NOT exercised in this environment.
    """

    async def iter_pending(self) -> AsyncIterator[MigrationItem]:
        """Yield each document pending migration, one at a time."""
        ...

    async def count_pending(self) -> int:
        """Return the total number of pending documents (for dry-run sizing)."""
        ...


@runtime_checkable
class MigrationProgressStore(Protocol):
    """Per-document idempotency / recovery ledger (SRS §8.8 idempotent rerun).

    Records the latest ``MigrationTaskResult`` for each ``document_id`` so a
    rerun / ``resume`` can skip already-SWITCHED docs and retry FAILED ones
    without re-scanning the whole inventory.
    """

    async def get(self, document_id: str) -> MigrationTaskResult | None:
        """Return the last recorded result for ``document_id``, or None."""
        ...

    async def upsert(self, result: MigrationTaskResult) -> None:
        """Insert or replace the result row for ``result.document_id``."""
        ...

    async def list_failed(self) -> list[MigrationTaskResult]:
        """Return all recorded results whose status is FAILED."""
        ...

    async def list_pending(self) -> list[MigrationTaskResult]:
        """Return all recorded results that are not yet SWITCHED.

        Includes PENDING / UPLOADING / VERIFIED / FAILED — anything that did
        not reach terminal success and should be retried by ``resume``.
        """
        ...


__all__ = [
    "MigrationInventory",
    "MigrationItem",
    "MigrationProgressStore",
    "MigrationReport",
    "MigrationTaskResult",
    "MigrationTaskStatus",
    "REASON_HASH_CONFLICT",
    "REASON_MISSING_FILE",
    "REASON_ORPHAN",
    "REASON_PERMISSION",
    "REASON_REVISION_CONFLICT",
    "REASON_UNKNOWN",
]
