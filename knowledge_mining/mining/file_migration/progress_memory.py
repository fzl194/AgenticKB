"""In-memory ``MigrationProgressStore`` (M1.5, WP1C; ADR-0003 D-025).

Used by the service test suite so the full migration lifecycle runs without
PostgreSQL. The PG implementation (``progress_pg``) is a separate, PG-gated
module that maps each ``MigrationTaskResult`` to a row in a migration ledger
table.

All methods are ``async`` to match the Protocol (the PG impl is genuinely
async via psycopg).
"""
from __future__ import annotations

from knowledge_mining.mining.file_migration.contracts import (
    MigrationProgressStore,
    MigrationTaskResult,
    MigrationTaskStatus,
)


class MemoryMigrationProgressStore(MigrationProgressStore):
    """In-memory ``MigrationProgressStore`` keyed by ``document_id``.

    ``upsert`` is a last-write-wins replace keyed by ``document_id``; the
    service always writes the full latest result so this is sufficient for
    idempotent recovery (SRS §8.8).
    """

    def __init__(self) -> None:
        self._by_doc: dict[str, MigrationTaskResult] = {}

    async def get(self, document_id: str) -> MigrationTaskResult | None:
        return self._by_doc.get(document_id)

    async def upsert(self, result: MigrationTaskResult) -> None:
        self._by_doc[result.document_id] = result

    async def list_failed(self) -> list[MigrationTaskResult]:
        return [
            r
            for r in self._by_doc.values()
            if r.status == MigrationTaskStatus.FAILED
        ]

    async def list_pending(self) -> list[MigrationTaskResult]:
        # Anything not SWITCHED is retryable by ``resume`` (SRS §8.8 rerun).
        return [
            r
            for r in self._by_doc.values()
            if r.status != MigrationTaskStatus.SWITCHED
        ]

    # Test-helper (not part of the Protocol): total recorded rows.
    def __len__(self) -> int:
        return len(self._by_doc)


__all__ = ["MemoryMigrationProgressStore"]
