"""PostgreSQL repositories for the File Management layer (M1.2, WP1B).

Implements the retained Protocols in ``contracts/file_management.py`` over a
psycopg ``AsyncConnectionPool`` (the same pool type used by
``mining/kb/db.py``). Each method opens its own connection (one logical
transaction); ``content_revision`` is enforced server-side via
``WHERE ... = %s ... RETURNING``.

This module is imported lazily and only exercised when a real PostgreSQL test
DB is available (``KB_RUN_POSTGRES_ACCEPTANCE=1``). Without PG the smoke tests
in ``tests/file_management/test_repositories_pg.py`` skip. The service test
suite uses the in-memory fakes and never touches this module.

Column mapping (008 DDL):
- ``asset_storage_objects``: id/provider/bucket/object_key/object_version_id/
  sha256/size/mime/etag/artifact_class/encryption/state/retention_until/
  created_at/last_verified_at
- ``asset_documents``: storage_object_id/source_raw_hash/content_revision/
  content_updated_at/deleted_at/restored_at (added 008)

References:
- SRS §4.1A (upload transaction), §4.3A (optimistic concurrency), §8.5 (DDL),
  §9.5 (recovery), §C01 (error codes).
- ADR-0003 D-006 (guarded PG), D-017 (COALESCE version_id ''), D-022
  (Repository Protocol + service layering).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from psycopg.rows import dict_row

from knowledge_mining.mining.contracts.file_management import (
    DocumentCurrentContent,
    DocumentRevisionConflict,
    DocumentRow,
    StorageObjectRecord,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Row -> frozen-record adapters
# ---------------------------------------------------------------------------


def _storage_object_from_row(r: dict[str, Any]) -> StorageObjectRecord:
    return StorageObjectRecord(
        id=r["id"],
        provider=r["provider"],
        bucket=r["bucket"],
        object_key=r["object_key"],
        object_version_id=r["object_version_id"],
        sha256=r["sha256"],
        size=r["size"],
        mime=r["mime"],
        etag=r.get("etag"),
        artifact_class=r["artifact_class"],
        encryption=r.get("encryption"),
        state=r["state"],
        retention_until=r.get("retention_until"),
        created_at=_iso(r.get("created_at")),
        last_verified_at=_iso(r.get("last_verified_at")),
    )


def _document_from_row(r: dict[str, Any]) -> DocumentCurrentContent:
    return DocumentCurrentContent(
        document_id=r["id"],
        storage_object_id=r["storage_object_id"],
        source_raw_hash=r["source_raw_hash"],
        content_revision=r["content_revision"],
        content_updated_at=_iso(r.get("content_updated_at")),
    )


def _document_row_from_row(r: dict[str, Any]) -> DocumentRow:
    """Full ``asset_documents`` row -> :class:`DocumentRow` (M1.3)."""
    return DocumentRow(
        document_id=r["id"],
        kb_id=r["kb_id"],
        folder_id=r.get("folder_id"),
        document_name=r.get("document_name"),
        storage_object_id=r.get("storage_object_id"),
        source_raw_hash=r.get("source_raw_hash"),
        content_revision=int(r.get("content_revision") or 0),
        deleted_at=_iso(r.get("deleted_at")) or None,
    )


def _iso(value: Any) -> str:
    """Normalize a PG timestamp/datetime to an ISO string (or '')."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    # datetime/timestamptz from psycopg
    return value.isoformat()


# ---------------------------------------------------------------------------
# StorageObjectRepository (PG)
# ---------------------------------------------------------------------------


class PgStorageObjectRepository:
    """PG ``StorageObjectRepository`` over ``asset_storage_objects``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def register(self, record: StorageObjectRecord) -> StorageObjectRecord:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO asset_storage_objects
                       (id, provider, bucket, object_key, object_version_id,
                        sha256, size, mime, etag, artifact_class, encryption,
                        state, retention_until, created_at, last_verified_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING
                   RETURNING *""",
                (
                    record.id, record.provider, record.bucket, record.object_key,
                    record.object_version_id, record.sha256, record.size, record.mime,
                    record.etag, record.artifact_class, record.encryption,
                    record.state, record.retention_until,
                    record.created_at or _utcnow(), record.last_verified_at,
                ),
            )
            row = await cur.fetchone()
            if row is None:
                # Conflict on the COALESCE location unique index: fetch existing.
                existing = await self.find_by_location(
                    record.bucket, record.object_key, record.object_version_id
                )
                assert existing is not None
                return existing
            return _storage_object_from_row(dict(row))

    async def get(self, storage_object_id: str) -> StorageObjectRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM asset_storage_objects WHERE id = %s",
                [storage_object_id],
            )
            row = await cur.fetchone()
            return _storage_object_from_row(dict(row)) if row else None

    async def find_by_location(
        self,
        bucket: str,
        object_key: str,
        version_id: str | None = None,
    ) -> StorageObjectRecord | None:
        # COALESCE normalizes NULL version_id to '' (D-017).
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM asset_storage_objects
                   WHERE bucket = %s AND object_key = %s
                     AND COALESCE(object_version_id, '') = COALESCE(%s, '')""",
                [bucket, object_key, version_id],
            )
            row = await cur.fetchone()
            return _storage_object_from_row(dict(row)) if row else None

    async def set_state(self, storage_object_id: str, state: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE asset_storage_objects SET state = %s WHERE id = %s",
                [state, storage_object_id],
            )

    async def mark_verified(self, storage_object_id: str, at: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """UPDATE asset_storage_objects
                   SET last_verified_at = %s WHERE id = %s""",
                [at, storage_object_id],
            )


# ---------------------------------------------------------------------------
# DocumentCurrentContentRepository (PG)
# ---------------------------------------------------------------------------


class PgDocumentCurrentContentRepository:
    """PG ``DocumentCurrentContentRepository`` over ``asset_documents``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get(self, document_id: str) -> DocumentCurrentContent | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, storage_object_id, source_raw_hash, content_revision,
                          content_updated_at
                   FROM asset_documents WHERE id = %s""",
                [document_id],
            )
            row = await cur.fetchone()
            if row is None or dict(row).get("storage_object_id") is None:
                return None
            return _document_from_row(dict(row))

    async def create_document(
        self,
        *,
        kb_id: str,
        document_id: str,
        folder_id: str | None,
        owner_id: str | None,
        domain: str | None = None,  # None → 从 knowledge_bases 解析（SRS §A12 KB 隔离）
        document_name: str | None,
        document_type: str | None,
        storage_object_id: str,
        source_raw_hash: str,
    ) -> DocumentCurrentContent:
        now = _utcnow()
        async with self._pool.connection() as conn:
            if domain is None:
                cur = await conn.execute(
                    "SELECT domain FROM knowledge_bases WHERE id = %s AND status = 'active'",
                    [kb_id],
                )
                kb_row = await cur.fetchone()
                if kb_row is None:
                    raise ValueError(
                        f"knowledge base not found or deleted: {kb_id!r} — refusing to "
                        "create document without its domain (SRS §A12, no ghost documents)"
                    )
                domain = kb_row["domain"]
            # document_key：迁移期兼容别名（ADR-0002 §15.1 #8），由文件名派生。
            document_key = f"doc:/{document_name or document_id}"
            cur = await conn.execute(
                """INSERT INTO asset_documents
                       (id, kb_id, domain, document_key, folder_id, owner_id,
                        document_name, document_type, storage_object_id,
                        source_raw_hash, content_revision, content_updated_at,
                        created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)
                   RETURNING id, storage_object_id, source_raw_hash,
                             content_revision, content_updated_at""",
                (
                    document_id, kb_id, domain, document_key, folder_id, owner_id,
                    document_name, document_type, storage_object_id,
                    source_raw_hash, now, now,
                ),
            )
            row = await cur.fetchone()
            if row is None:
                raise ValueError(f"document already exists: {document_id}")
            return _document_from_row(dict(row))

    async def set_current_content(
        self,
        document_id: str,
        storage_object_id: str,
        raw_hash: str,
        *,
        expected_revision: int,
    ) -> DocumentCurrentContent:
        now = _utcnow()
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE asset_documents SET
                       storage_object_id = %s,
                       source_raw_hash = %s,
                       content_revision = content_revision + 1,
                       content_updated_at = %s
                   WHERE id = %s AND content_revision = %s
                   RETURNING id, storage_object_id, source_raw_hash,
                             content_revision, content_updated_at""",
                [storage_object_id, raw_hash, now, document_id, expected_revision],
            )
            row = await cur.fetchone()
            if row is None:
                # Either the row is gone, or the revision did not match.
                cur2 = await conn.execute(
                    "SELECT content_revision FROM asset_documents WHERE id = %s",
                    [document_id],
                )
                current = await cur2.fetchone()
                actual = int(dict(current)["content_revision"]) if current else -1
                raise DocumentRevisionConflict(
                    document_id=document_id,
                    expected=expected_revision,
                    actual=actual,
                )
            return _document_from_row(dict(row))

    async def mark_outdated(self, document_id: str) -> None:
        # Lifecycle hint only; no dedicated column yet. No-op for v1.
        return

    # -- M1.3 directory-management methods (list/rename/move/soft_delete/restore)

    async def get_row(self, document_id: str) -> DocumentRow | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, kb_id, folder_id, document_name, storage_object_id,
                          source_raw_hash, content_revision, deleted_at
                   FROM asset_documents WHERE id = %s""",
                [document_id],
            )
            row = await cur.fetchone()
            return _document_row_from_row(dict(row)) if row else None

    async def list_in_kb(
        self,
        kb_id: str,
        *,
        folder_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[DocumentRow]:
        sql = (
            "SELECT id, kb_id, folder_id, document_name, storage_object_id, "
            "source_raw_hash, content_revision, deleted_at "
            "FROM asset_documents WHERE kb_id = %s"
        )
        params: list[Any] = [kb_id]
        if folder_id is not None:
            sql += " AND folder_id IS NOT DISTINCT FROM %s"
            params.append(folder_id)
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY document_name NULLS LAST, id"
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return [_document_row_from_row(dict(r)) for r in await cur.fetchall()]

    async def rename(self, document_id: str, new_name: str) -> DocumentRow:
        return await self._update_row(
            "UPDATE asset_documents SET document_name = %s WHERE id = %s",
            [new_name, document_id],
            document_id,
        )

    async def move(
        self, document_id: str, target_folder_id: str | None
    ) -> DocumentRow:
        return await self._update_row(
            "UPDATE asset_documents SET folder_id = %s WHERE id = %s",
            [target_folder_id, document_id],
            document_id,
        )

    async def set_deleted(self, document_id: str) -> DocumentRow:
        return await self._update_row(
            "UPDATE asset_documents SET deleted_at = %s WHERE id = %s",
            [_utcnow(), document_id],
            document_id,
        )

    async def clear_deleted(self, document_id: str) -> DocumentRow:
        return await self._update_row(
            "UPDATE asset_documents SET deleted_at = NULL WHERE id = %s",
            [document_id],
            document_id,
        )

    async def _update_row(
        self, sql: str, params: list[Any], document_id: str
    ) -> DocumentRow:
        async with self._pool.connection() as conn:
            await conn.execute(sql, params)
        row = await self.get_row(document_id)
        if row is None:
            raise KeyError(f"document not found: {document_id}")
        return row


__all__ = [
    "PgDocumentCurrentContentRepository",
    "PgStorageObjectRepository",
]


_ = dict_row
