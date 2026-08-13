"""PostgreSQL repositories for the File Management layer (M1.2, WP1B).

Implements the five Protocols in ``contracts/file_management.py`` over a
psycopg ``AsyncConnectionPool`` (the same pool type used by
``mining/kb/db.py``). Each method opens its own connection (one logical
transaction); the optimistic-concurrency fields (``content_revision``,
``quota.version``) are enforced server-side via ``WHERE ... = %s ... RETURNING``.

This module is imported lazily and only exercised when a real PostgreSQL test
DB is available (``KB_RUN_POSTGRES_ACCEPTANCE=1``). Without PG the smoke tests
in ``tests/file_management/test_repositories_pg.py`` skip. The service test
suite uses the in-memory fakes and never touches this module.

Column mapping (008 DDL + M1.2 incremental ``staging_bucket`` /
``committed_storage_object_id`` / ``committed_document_id`` columns):
- ``asset_storage_objects``: id/provider/bucket/object_key/object_version_id/
  sha256/size/mime/etag/artifact_class/encryption/state/retention_until/
  created_at/last_verified_at
- ``asset_upload_sessions``: + staging_bucket/committed_storage_object_id/
  committed_document_id (added M1.2)
- ``asset_documents``: storage_object_id/source_raw_hash/content_revision/
  content_updated_at/deleted_at/restored_at (added 008)
- ``asset_file_audit_events``: id/kb_id/document_id/storage_object_id/
  content_revision/actor/action/before_json/after_json/created_at
- ``asset_storage_quotas``: kb_id/limit_bytes/reserved_bytes/used_bytes/
  version/updated_at

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
    FileAuditEvent,
    QuotaExceeded,
    QuotaRecord,
    StorageObjectRecord,
    UploadSessionRecord,
    dataclass_replace,
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


def _upload_session_from_row(r: dict[str, Any]) -> UploadSessionRecord:
    return UploadSessionRecord(
        id=r["id"],
        kb_id=r["kb_id"],
        folder_id=r.get("folder_id"),
        actor=r["actor"],
        original_filename=r["original_filename"],
        expected_size=r.get("expected_size"),
        expected_mime=r.get("expected_mime"),
        staging_bucket=r.get("staging_bucket"),
        staging_object_key=r["staging_object_key"],
        idempotency_key=r["idempotency_key"],
        expires_at=_iso(r.get("expires_at")),
        state=r["state"],
        error_message=r.get("error_message"),
        committed_storage_object_id=r.get("committed_storage_object_id"),
        committed_document_id=r.get("committed_document_id"),
        created_at=_iso(r.get("created_at")),
        updated_at=_iso(r.get("updated_at")),
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


def _quota_from_row(r: dict[str, Any]) -> QuotaRecord:
    return QuotaRecord(
        kb_id=r["kb_id"],
        limit_bytes=r["limit_bytes"],
        reserved_bytes=r["reserved_bytes"],
        used_bytes=r["used_bytes"],
        version=r["version"],
        updated_at=_iso(r.get("updated_at")),
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
# UploadSessionRepository (PG)
# ---------------------------------------------------------------------------


class PgUploadSessionRepository:
    """PG ``UploadSessionRepository`` over ``asset_upload_sessions``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def create(self, record: UploadSessionRecord) -> UploadSessionRecord:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO asset_upload_sessions
                       (id, kb_id, folder_id, actor, original_filename,
                        expected_size, expected_mime, staging_bucket, staging_object_key,
                        idempotency_key, expires_at, state, error_message,
                        committed_storage_object_id, committed_document_id,
                        created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (kb_id, actor, idempotency_key) DO NOTHING
                   RETURNING *""",
                (
                    record.id, record.kb_id, record.folder_id, record.actor,
                    record.original_filename, record.expected_size, record.expected_mime,
                    record.staging_bucket, record.staging_object_key,
                    record.idempotency_key, record.expires_at, record.state,
                    record.error_message, record.committed_storage_object_id,
                    record.committed_document_id,
                    record.created_at or _utcnow(), record.updated_at or _utcnow(),
                ),
            )
            row = await cur.fetchone()
            if row is None:
                # Idempotent create conflict; fetch the existing by idem key.
                return await self.find_by_idempotency(
                    record.kb_id, record.actor, record.idempotency_key
                )  # type: ignore[return-value]
            return _upload_session_from_row(dict(row))

    async def get(self, session_id: str) -> UploadSessionRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM asset_upload_sessions WHERE id = %s",
                [session_id],
            )
            row = await cur.fetchone()
            return _upload_session_from_row(dict(row)) if row else None

    async def find_by_idempotency(
        self,
        kb_id: str,
        actor: str,
        idempotency_key: str,
    ) -> UploadSessionRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM asset_upload_sessions
                   WHERE kb_id = %s AND actor = %s AND idempotency_key = %s""",
                [kb_id, actor, idempotency_key],
            )
            row = await cur.fetchone()
            return _upload_session_from_row(dict(row)) if row else None

    async def update(self, session: UploadSessionRecord) -> UploadSessionRecord:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE asset_upload_sessions SET
                       state = %s,
                       error_message = %s,
                       committed_storage_object_id = %s,
                       committed_document_id = %s,
                       updated_at = %s
                   WHERE id = %s
                   RETURNING *""",
                (
                    session.state, session.error_message,
                    session.committed_storage_object_id,
                    session.committed_document_id,
                    _utcnow(), session.id,
                ),
            )
            row = await cur.fetchone()
            if row is None:
                raise KeyError(f"upload session not found: {session.id}")
            return _upload_session_from_row(dict(row))

    async def list_expired(self, now: str) -> list[UploadSessionRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM asset_upload_sessions
                   WHERE expires_at <= %s
                     AND state NOT IN ('COMMITTED','ABORTED','EXPIRED','REJECTED')
                   ORDER BY expires_at""",
                [now],
            )
            rows = await cur.fetchall()
            return [_upload_session_from_row(dict(r)) for r in rows]


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
        document_name: str | None,
        document_type: str | None,
        storage_object_id: str,
        source_raw_hash: str,
    ) -> DocumentCurrentContent:
        now = _utcnow()
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO asset_documents
                       (id, kb_id, folder_id, owner_id, document_name, document_type,
                        storage_object_id, source_raw_hash, content_revision,
                        content_updated_at, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)
                   RETURNING id, storage_object_id, source_raw_hash,
                             content_revision, content_updated_at""",
                (
                    document_id, kb_id, folder_id, owner_id, document_name,
                    document_type, storage_object_id, source_raw_hash, now, now,
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


# ---------------------------------------------------------------------------
# FileAuditRepository (PG)
# ---------------------------------------------------------------------------


class PgFileAuditRepository:
    """PG ``FileAuditRepository`` over ``asset_file_audit_events``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def append(self, event: FileAuditEvent) -> FileAuditEvent:
        import json

        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO asset_file_audit_events
                       (id, kb_id, document_id, storage_object_id, content_revision,
                        actor, action, before_json, after_json, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    event.id, event.kb_id, event.document_id,
                    event.storage_object_id, event.content_revision,
                    event.actor, event.action,
                    json.dumps(event.before_json, ensure_ascii=False),
                    json.dumps(event.after_json, ensure_ascii=False),
                    event.created_at or _utcnow(),
                ),
            )
            row = await cur.fetchone()
            r = dict(row)
            return FileAuditEvent(
                id=r["id"], kb_id=r["kb_id"], document_id=r.get("document_id"),
                storage_object_id=r.get("storage_object_id"),
                content_revision=r.get("content_revision"), actor=r["actor"],
                action=r["action"],
                before_json=r.get("before_json") or {},
                after_json=r.get("after_json") or {},
                created_at=_iso(r.get("created_at")),
            )


# ---------------------------------------------------------------------------
# QuotaRepository (PG) — optimistic concurrency, server-enforced
# ---------------------------------------------------------------------------


class PgQuotaRepository:
    """PG ``QuotaRepository`` over ``asset_storage_quotas``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get(self, kb_id: str) -> QuotaRecord:
        async with self._pool.connection() as conn:
            # Upsert a zero-limit default if absent (fail-closed).
            await conn.execute(
                """INSERT INTO asset_storage_quotas (kb_id, limit_bytes, updated_at)
                   VALUES (%s, 0, %s)
                   ON CONFLICT (kb_id) DO NOTHING""",
                [kb_id, _utcnow()],
            )
            cur = await conn.execute(
                "SELECT * FROM asset_storage_quotas WHERE kb_id = %s",
                [kb_id],
            )
            row = await cur.fetchone()
            return _quota_from_row(dict(row))

    async def reserve(
        self,
        kb_id: str,
        bytes_to_reserve: int,
        expected_version: int,
    ) -> QuotaRecord:
        # Enforce limit atomically in the same UPDATE: only advance if the new
        # reserved+used total stays within limit AND the version matches.
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE asset_storage_quotas SET
                       reserved_bytes = reserved_bytes + %s,
                       version = version + 1,
                       updated_at = %s
                   WHERE kb_id = %s AND version = %s
                     AND (reserved_bytes + used_bytes + %s) <= limit_bytes
                   RETURNING *""",
                [bytes_to_reserve, _utcnow(), kb_id, expected_version, bytes_to_reserve],
            )
            row = await cur.fetchone()
            if row is None:
                await self._raise_quota_conflict(conn, kb_id, expected_version)
            return _quota_from_row(dict(row))

    async def commit(
        self,
        kb_id: str,
        reserved_bytes_to_release: int,
        used_bytes_to_add: int,
        expected_version: int,
    ) -> QuotaRecord:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE asset_storage_quotas SET
                       reserved_bytes = reserved_bytes - %s,
                       used_bytes = used_bytes + %s,
                       version = version + 1,
                       updated_at = %s
                   WHERE kb_id = %s AND version = %s
                     AND reserved_bytes - %s >= 0
                     AND (reserved_bytes - %s + used_bytes + %s) <= limit_bytes
                   RETURNING *""",
                [
                    reserved_bytes_to_release, used_bytes_to_add, _utcnow(),
                    kb_id, expected_version,
                    reserved_bytes_to_release,
                    reserved_bytes_to_release, used_bytes_to_add,
                ],
            )
            row = await cur.fetchone()
            if row is None:
                await self._raise_quota_conflict(conn, kb_id, expected_version)
            return _quota_from_row(dict(row))

    async def release(
        self,
        kb_id: str,
        bytes_to_release: int,
        expected_version: int,
    ) -> QuotaRecord:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE asset_storage_quotas SET
                       reserved_bytes = reserved_bytes - %s,
                       version = version + 1,
                       updated_at = %s
                   WHERE kb_id = %s AND version = %s
                     AND reserved_bytes - %s >= 0
                   RETURNING *""",
                [bytes_to_release, _utcnow(), kb_id, expected_version, bytes_to_release],
            )
            row = await cur.fetchone()
            if row is None:
                await self._raise_quota_conflict(conn, kb_id, expected_version)
            return _quota_from_row(dict(row))

    @staticmethod
    async def _raise_quota_conflict(conn: Any, kb_id: str, expected_version: int) -> None:
        """Distinguish version-conflict from limit-overflow and raise."""
        cur = await conn.execute(
            "SELECT * FROM asset_storage_quotas WHERE kb_id = %s",
            [kb_id],
        )
        row = await cur.fetchone()
        if row is None:
            raise ValueError(f"quota row missing for kb {kb_id!r}")
        r = dict(row)
        if r["version"] != expected_version:
            raise ValueError(
                f"quota version conflict for kb {kb_id!r}: "
                f"expected {expected_version}, actual {r['version']}"
            )
        # Version matched but the guard failed => limit exceeded.
        raise QuotaExceeded(
            f"quota exceeded for kb {kb_id!r}: "
            f"reserved={r['reserved_bytes']} used={r['used_bytes']} "
            f"limit={r['limit_bytes']}"
        )


__all__ = [
    "PgDocumentCurrentContentRepository",
    "PgFileAuditRepository",
    "PgQuotaRepository",
    "PgStorageObjectRepository",
    "PgUploadSessionRepository",
]


# Silence unused-import lint for dataclass_replace (re-exported convenience).
_ = dataclass_replace
_ = dict_row
