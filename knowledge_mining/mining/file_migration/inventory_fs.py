"""Filesystem-backed ``MigrationInventory`` (M1.5, WP1C; ADR-0003 D-025).

``FilesystemMigrationInventory`` yields ``MigrationItem`` records from a
pre-built Python list (the dev / test path) or from a small JSON manifest file
(the operator path). It NEVER queries PostgreSQL — the PG variant
(``inventory_pg``) is a separate, PG-gated module.

The inventory is intentionally a thin enumerator: it does not stat the files
(the service does that lazily so a missing file is reported as a per-document
``missing_file`` failure, SRS §8.8). The ``size_hint`` / ``mime_hint`` fields
are advisory only and may be None.

References:
- SRS §8.8 Phase 0 (inventory) + Phase 2 (backfill).
- ADR-0003 D-004 (no read/write path changes), D-025 (this package's scope).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

from knowledge_mining.mining.file_migration.contracts import (
    MigrationInventory,
    MigrationItem,
)


class FilesystemMigrationInventory(MigrationInventory):
    """``MigrationInventory`` backed by an in-memory list or a JSON manifest.

    Two construction modes:

    1. ``FilesystemMigrationInventory(items=[...])`` — used by tests: the
       caller builds ``MigrationItem`` records pointing at real files under
       ``tmp_path`` and the inventory simply yields them. This keeps the file
       fixtures visible in the test body.
    2. ``FilesystemMigrationInventory.from_manifest(path)`` — used by operators
       in dev: reads a JSON file that is a list of objects with the
       ``MigrationItem`` fields and yields one item per entry. Useful for
       staging a curated subset of legacy rows before wiring the PG inventory.

    Both modes are sync at construction time; iteration is ``async`` only to
    satisfy the ``MigrationInventory`` Protocol (the PG variant is genuinely
    async via psycopg).
    """

    def __init__(self, items: Iterable[MigrationItem] | None = None) -> None:
        self._items: list[MigrationItem] = list(items or [])

    @classmethod
    def from_manifest(cls, path: str | Path) -> "FilesystemMigrationInventory":
        """Load items from a JSON manifest file (list of MigrationItem dicts).

        Each entry must contain at least ``document_id``, ``kb_id``,
        ``storage_path`` and ``current_content_revision``; ``size_hint`` and
        ``mime_hint`` are optional.
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(
                f"manifest must be a JSON list of items, got {type(raw).__name__}"
            )
        items = [cls._item_from_dict(entry) for entry in raw]
        return cls(items=items)

    @staticmethod
    def _item_from_dict(entry: dict) -> MigrationItem:
        required = ("document_id", "kb_id", "storage_path", "current_content_revision")
        missing = [k for k in required if k not in entry]
        if missing:
            raise ValueError(f"manifest entry missing keys: {missing}")
        return MigrationItem(
            document_id=str(entry["document_id"]),
            kb_id=str(entry["kb_id"]),
            storage_path=str(entry["storage_path"]),
            current_content_revision=int(entry["current_content_revision"]),
            size_hint=entry.get("size_hint"),
            mime_hint=entry.get("mime_hint"),
        )

    async def iter_pending(self) -> AsyncIterator[MigrationItem]:
        """Yield each pending item in insertion order.

        Note: this is an ``async def`` generator — the ``async for`` in the
        caller drives it lazily. The PG variant will yield rows from an async
        cursor here.
        """
        for item in self._items:
            yield item

    async def count_pending(self) -> int:
        return len(self._items)


__all__ = ["FilesystemMigrationInventory"]
