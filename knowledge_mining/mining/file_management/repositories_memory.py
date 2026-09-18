"""In-memory fake repositories for the File Management layer (M1.2).

Implements the Protocols in ``contracts/file_management.py`` backed by
plain ``dict`` stores. Used by test suites (and local dev) so the
full flow runs without PostgreSQL (ADR-0003 D-006, D-022).

Concurrency model:
- ``DocumentCurrentContent.content_revision`` is checked against the current
  in-memory value; on mismatch the same errors the PG implementation raises
  are raised here.
- ``find_by_location`` is the dedup probe for storage objects.

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
    StorageObjectRecord,
)
from knowledge_mining.mining.contracts.storage.enums import VALID_ARTIFACT_CLASSES
from knowledge_mining.mining.contracts.state_machines import (
    assert_transition,
)


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
        domain: str | None = None,  # memory 无 knowledge_bases 表，默认 generic
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
            "domain": domain or "generic",
            "document_key": f"doc:/{document_name or document_id}",
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


__all__ = [
    "MemoryDocumentCurrentContentRepository",
    "MemoryStorageObjectRepository",
]
