"""In-memory fake repositories for the File Management layer (M1.2).

Implements the five Protocols in ``contracts/file_management.py`` backed by
plain ``dict`` stores. Used by the service test suite (and local dev) so the
full upload-session flow runs without PostgreSQL (ADR-0003 D-006, D-022).

Concurrency model:
- Optimistic-concurrency fields (``QuotaRecord.version``,
  ``DocumentCurrentContent.content_revision``) are checked against the current
  in-memory value; on mismatch the same errors the PG implementation raises
  are raised here.
- ``find_by_location`` is the dedup probe for storage objects.
- ``list_expired`` filters non-terminal sessions whose ``expires_at`` <= now.

All methods are ``async`` to match the Protocol signatures (the PG impl is
genuinely async via psycopg).
"""
from __future__ import annotations

import uuid
from typing import Any

from knowledge_mining.mining.contracts.file_management import (
    DocumentCurrentContent,
    DocumentRevisionConflict,
    DocumentRow,
    FileAuditEvent,
    QuotaExceeded,
    QuotaRecord,
    StorageObjectRecord,
    UploadSessionRecord,
)
from knowledge_mining.mining.contracts.storage.enums import VALID_ARTIFACT_CLASSES
from knowledge_mining.mining.contracts.state_machines import (
    TERMINAL_STATES,
    assert_transition,
)

# Upload session terminal states (SRS §9.0A) — used by list_expired.
_UPLOAD_SESSION_TERMINAL = TERMINAL_STATES["upload_session"]


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _location_key(bucket: str, object_key: str, version_id: str | None) -> tuple:
    """Normalize nullable version_id to '' for the dedup index (D-017)."""
    return (bucket, object_key, version_id or "")


class MemoryStorageObjectRepository:
    """In-memory ``StorageObjectRepository``."""

    def __init__(self) -> None:
        self._by_id: dict[str, StorageObjectRecord] = {}
        self._by_location: dict[tuple, str] = {}

    async def register(self, record: StorageObjectRecord) -> StorageObjectRecord:
        # Idempotent on location: if a record already exists at this location,
        # return it (dedup path — the service probes first, but be defensive).
        key = _location_key(record.bucket, record.object_key, record.object_version_id)
        existing_id = self._by_location.get(key)
        if existing_id is not None:
            return self._by_id[existing_id]
        if record.artifact_class not in VALID_ARTIFACT_CLASSES:
            raise ValueError(f"unknown artifact_class: {record.artifact_class!r}")
        self._by_id[record.id] = record
        self._by_location[key] = record.id
        return record

    async def get(self, storage_object_id: str) -> StorageObjectRecord | None:
        return self._by_id.get(storage_object_id)

    async def find_by_location(
        self,
        bucket: str,
        object_key: str,
        version_id: str | None = None,
    ) -> StorageObjectRecord | None:
        key = _location_key(bucket, object_key, version_id)
        rid = self._by_location.get(key)
        return self._by_id[rid] if rid else None

    async def set_state(self, storage_object_id: str, state: str) -> None:
        rec = self._by_id.get(storage_object_id)
        if rec is None:
            raise KeyError(f"storage object not found: {storage_object_id}")
        # Validate the transition against the storage object state machine.
        assert_transition("storage_object", rec.state, state)
        self._by_id[storage_object_id] = StorageObjectRecord(
            **{**rec.__dict__, "state": state}
        )

    async def mark_verified(self, storage_object_id: str, at: str) -> None:
        rec = self._by_id.get(storage_object_id)
        if rec is None:
            raise KeyError(f"storage object not found: {storage_object_id}")
        self._by_id[storage_object_id] = StorageObjectRecord(
            **{**rec.__dict__, "last_verified_at": at}
        )


class MemoryUploadSessionRepository:
    """In-memory ``UploadSessionRepository``."""

    def __init__(self) -> None:
        self._by_id: dict[str, UploadSessionRecord] = {}
        self._by_idem: dict[tuple[str, str, str], str] = {}

    async def create(self, record: UploadSessionRecord) -> UploadSessionRecord:
        idem_key = (record.kb_id, record.actor, record.idempotency_key)
        if idem_key in self._by_idem:
            # Idempotent create: return the existing session unchanged.
            return self._by_id[self._by_idem[idem_key]]
        self._by_id[record.id] = record
        self._by_idem[idem_key] = record.id
        return record

    async def get(self, session_id: str) -> UploadSessionRecord | None:
        return self._by_id.get(session_id)

    async def find_by_idempotency(
        self,
        kb_id: str,
        actor: str,
        idempotency_key: str,
    ) -> UploadSessionRecord | None:
        rid = self._by_idem.get((kb_id, actor, idempotency_key))
        return self._by_id[rid] if rid else None

    async def update(self, session: UploadSessionRecord) -> UploadSessionRecord:
        if session.id not in self._by_id:
            raise KeyError(f"upload session not found: {session.id}")
        updated = session.with_updates(updated_at=_utcnow())
        self._by_id[session.id] = updated
        return updated

    async def list_expired(self, now: str) -> list[UploadSessionRecord]:
        return [
            s
            for s in self._by_id.values()
            if s.state not in _UPLOAD_SESSION_TERMINAL and s.expires_at <= now
        ]


class MemoryDocumentCurrentContentRepository:
    """In-memory ``DocumentCurrentContentRepository``.

    Stores the document row skeleton (only the current-content fields) keyed
    by ``document_id``. The revision is the optimistic-concurrency guard.
    """

    def __init__(self) -> None:
        self._docs: dict[str, dict[str, Any]] = {}

    async def get(self, document_id: str) -> DocumentCurrentContent | None:
        row = self._docs.get(document_id)
        if row is None:
            return None
        return DocumentCurrentContent(
            document_id=document_id,
            storage_object_id=row["storage_object_id"],
            source_raw_hash=row["source_raw_hash"],
            content_revision=row["content_revision"],
            content_updated_at=row.get("content_updated_at"),
        )

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
        if document_id in self._docs:
            raise ValueError(f"document already exists: {document_id}")
        now = _utcnow()
        self._docs[document_id] = {
            "kb_id": kb_id,
            "folder_id": folder_id,
            "owner_id": owner_id,
            "document_name": document_name,
            "document_type": document_type,
            "storage_object_id": storage_object_id,
            "source_raw_hash": source_raw_hash,
            "content_revision": 1,
            "content_updated_at": now,
        }
        return DocumentCurrentContent(
            document_id=document_id,
            storage_object_id=storage_object_id,
            source_raw_hash=source_raw_hash,
            content_revision=1,
            content_updated_at=now,
        )

    async def set_current_content(
        self,
        document_id: str,
        storage_object_id: str,
        raw_hash: str,
        *,
        expected_revision: int,
    ) -> DocumentCurrentContent:
        row = self._docs.get(document_id)
        if row is None:
            raise KeyError(f"document not found: {document_id}")
        if row["content_revision"] != expected_revision:
            raise DocumentRevisionConflict(
                document_id=document_id,
                expected=expected_revision,
                actual=row["content_revision"],
            )
        now = _utcnow()
        new_revision = row["content_revision"] + 1
        row["storage_object_id"] = storage_object_id
        row["source_raw_hash"] = raw_hash
        row["content_revision"] = new_revision
        row["content_updated_at"] = now
        return DocumentCurrentContent(
            document_id=document_id,
            storage_object_id=storage_object_id,
            source_raw_hash=raw_hash,
            content_revision=new_revision,
            content_updated_at=now,
        )

    async def mark_outdated(self, document_id: str) -> None:
        # Lifecycle hint only; the memory store records it but does not block.
        row = self._docs.get(document_id)
        if row is not None:
            row["outdated"] = True

    # -- M1.3 directory-management methods (list/rename/move/soft_delete/restore)

    @staticmethod
    def _to_row(doc_id: str, row: dict[str, Any]) -> DocumentRow:
        return DocumentRow(
            document_id=doc_id,
            kb_id=row["kb_id"],
            folder_id=row.get("folder_id"),
            document_name=row.get("document_name"),
            storage_object_id=row.get("storage_object_id"),
            source_raw_hash=row.get("source_raw_hash"),
            content_revision=int(row.get("content_revision", 0)),
            deleted_at=row.get("deleted_at"),
        )

    async def get_row(self, document_id: str) -> DocumentRow | None:
        row = self._docs.get(document_id)
        return self._to_row(document_id, row) if row is not None else None

    async def list_in_kb(
        self,
        kb_id: str,
        *,
        folder_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[DocumentRow]:
        out: list[DocumentRow] = []
        for doc_id, row in self._docs.items():
            if row.get("kb_id") != kb_id:
                continue
            if folder_id is not None and row.get("folder_id") != folder_id:
                continue
            if not include_deleted and row.get("deleted_at") is not None:
                continue
            out.append(self._to_row(doc_id, row))
        return out

    async def rename(self, document_id: str, new_name: str) -> DocumentRow:
        row = self._require_row(document_id)
        row["document_name"] = new_name
        return self._to_row(document_id, row)

    async def move(
        self, document_id: str, target_folder_id: str | None
    ) -> DocumentRow:
        row = self._require_row(document_id)
        row["folder_id"] = target_folder_id
        return self._to_row(document_id, row)

    async def set_deleted(self, document_id: str) -> DocumentRow:
        row = self._require_row(document_id)
        row["deleted_at"] = _utcnow()
        return self._to_row(document_id, row)

    async def clear_deleted(self, document_id: str) -> DocumentRow:
        row = self._require_row(document_id)
        row["deleted_at"] = None
        return self._to_row(document_id, row)

    def _require_row(self, document_id: str) -> dict[str, Any]:
        row = self._docs.get(document_id)
        if row is None:
            raise KeyError(f"document not found: {document_id}")
        return row


class MemoryFileAuditRepository:
    """In-memory ``FileAuditRepository`` (append-only, ordered)."""

    def __init__(self) -> None:
        self._events: list[FileAuditEvent] = []

    async def append(self, event: FileAuditEvent) -> FileAuditEvent:
        # Assign an id + created_at if the caller did not supply them.
        stored = FileAuditEvent(
            id=event.id or _new_id("audit"),
            kb_id=event.kb_id,
            document_id=event.document_id,
            storage_object_id=event.storage_object_id,
            content_revision=event.content_revision,
            actor=event.actor,
            action=event.action,
            before_json=dict(event.before_json),
            after_json=dict(event.after_json),
            created_at=event.created_at or _utcnow(),
        )
        self._events.append(stored)
        return stored

    # Test-helper (not part of the Protocol): ordered view of all events.
    def all(self) -> list[FileAuditEvent]:
        return list(self._events)

    def by_document(self, document_id: str) -> list[FileAuditEvent]:
        return [e for e in self._events if e.document_id == document_id]


class MemoryQuotaRepository:
    """In-memory ``QuotaRepository`` with optimistic concurrency."""

    def __init__(self) -> None:
        self._rows: dict[str, QuotaRecord] = {}

    def seed(self, kb_id: str, limit_bytes: int) -> QuotaRecord:
        """Test/dev helper: pre-create a quota row with a real limit."""
        row = QuotaRecord(
            kb_id=kb_id,
            limit_bytes=limit_bytes,
            reserved_bytes=0,
            used_bytes=0,
            version=1,
            updated_at=_utcnow(),
        )
        self._rows[kb_id] = row
        return row

    async def get(self, kb_id: str) -> QuotaRecord:
        row = self._rows.get(kb_id)
        if row is None:
            # Fail closed: a non-existent quota has limit 0 so any reserve
            # raises QuotaExceeded instead of bypassing the check.
            return QuotaRecord(
                kb_id=kb_id,
                limit_bytes=0,
                reserved_bytes=0,
                used_bytes=0,
                version=1,
                updated_at=_utcnow(),
            )
        return row

    def _check_version(self, kb_id: str, expected_version: int) -> QuotaRecord:
        row = self._rows.get(kb_id)
        if row is None:
            # Initialize a zero-limit row so the version check applies; the
            # caller's expected_version must match the implicit default (1).
            row = QuotaRecord(
                kb_id=kb_id,
                limit_bytes=0,
                reserved_bytes=0,
                used_bytes=0,
                version=1,
                updated_at=_utcnow(),
            )
            self._rows[kb_id] = row
        if row.version != expected_version:
            raise ValueError(
                f"quota version conflict for {kb_id!r}: "
                f"expected {expected_version}, actual {row.version}"
            )
        return row

    @staticmethod
    def _enforce_limit(kb_id: str, reserved: int, used: int, limit: int) -> None:
        if reserved + used > limit:
            raise QuotaExceeded(
                f"quota exceeded for kb {kb_id!r}: "
                f"reserved={reserved} used={used} limit={limit}"
            )

    async def reserve(
        self,
        kb_id: str,
        bytes_to_reserve: int,
        expected_version: int,
    ) -> QuotaRecord:
        row = self._check_version(kb_id, expected_version)
        new_reserved = row.reserved_bytes + bytes_to_reserve
        self._enforce_limit(kb_id, new_reserved, row.used_bytes, row.limit_bytes)
        updated = QuotaRecord(
            kb_id=kb_id,
            limit_bytes=row.limit_bytes,
            reserved_bytes=new_reserved,
            used_bytes=row.used_bytes,
            version=row.version + 1,
            updated_at=_utcnow(),
        )
        self._rows[kb_id] = updated
        return updated

    async def commit(
        self,
        kb_id: str,
        reserved_bytes_to_release: int,
        used_bytes_to_add: int,
        expected_version: int,
    ) -> QuotaRecord:
        row = self._check_version(kb_id, expected_version)
        new_reserved = row.reserved_bytes - reserved_bytes_to_release
        new_used = row.used_bytes + used_bytes_to_add
        if new_reserved < 0:
            raise ValueError(
                f"quota commit underflow for {kb_id!r}: "
                f"reserved would go negative ({new_reserved})"
            )
        self._enforce_limit(kb_id, new_reserved, new_used, row.limit_bytes)
        updated = QuotaRecord(
            kb_id=kb_id,
            limit_bytes=row.limit_bytes,
            reserved_bytes=new_reserved,
            used_bytes=new_used,
            version=row.version + 1,
            updated_at=_utcnow(),
        )
        self._rows[kb_id] = updated
        return updated

    async def release(
        self,
        kb_id: str,
        bytes_to_release: int,
        expected_version: int,
    ) -> QuotaRecord:
        row = self._check_version(kb_id, expected_version)
        new_reserved = row.reserved_bytes - bytes_to_release
        if new_reserved < 0:
            raise ValueError(
                f"quota release underflow for {kb_id!r}: "
                f"reserved would go negative ({new_reserved})"
            )
        updated = QuotaRecord(
            kb_id=kb_id,
            limit_bytes=row.limit_bytes,
            reserved_bytes=new_reserved,
            used_bytes=row.used_bytes,
            version=row.version + 1,
            updated_at=_utcnow(),
        )
        self._rows[kb_id] = updated
        return updated


__all__ = [
    "MemoryDocumentCurrentContentRepository",
    "MemoryFileAuditRepository",
    "MemoryQuotaRepository",
    "MemoryStorageObjectRepository",
    "MemoryUploadSessionRepository",
]
