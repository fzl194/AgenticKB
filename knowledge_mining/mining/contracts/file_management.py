"""Repository contracts for the File Management / Upload Session layer (M1.2).

This module is the hexagonal seam between the orchestration service
(``file_management.service.UploadSessionService``) and the persistence layer.
It defines five ``Protocol`` repositories plus the frozen record dataclasses
they exchange, and the file-management error subset of SRS §C01.

Design (ADR-0003 D-001, D-022):
- Pure stdlib. No psycopg / DB / FastAPI imports — the service depends on the
  ``Protocol``, and the PG implementation (``repositories_pg``) is injected at
  composition time. Tests inject the in-memory fake (``repositories_memory``)
  so the full service test suite runs without PostgreSQL.
- ``@runtime_checkable Protocol`` following the convention used by
  ``contracts/storage/port.py``.
- Frozen dataclasses; optimistic concurrency is explicit on
  ``DocumentCurrentContent.content_revision``.

The storage-object business identity (``storage_object_id``) is owned here, in
the Repository — the lower ``ObjectStorePort`` (D-020) only knows
``ObjectLocation``. This module is where the two models meet: a
``StorageObjectRecord`` carries both the business id and the physical location.

References:
- SRS §4.1A (upload transaction), §4.3 / §4.3A (document current content +
  operation semantics table), §C01 (error codes), §9.0A / §9.5 (upload session
  state machine + recovery).
- ADR-0003 D-002 (content dedup by sha256 — O3), D-020 (location addressing),
  D-022 (Repository Protocol + service layering).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from knowledge_mining.mining.contracts.storage.errors import StorageError


# ---------------------------------------------------------------------------
# Errors (SRS §C01, file-management subset)
# ---------------------------------------------------------------------------


class FileManagementError(StorageError):
    """Base class for file-management errors raised at the orchestration layer.

    Reuses the ``StorageError`` base so the existing error-to-HTTP mapping
    (``storage.errors`` docstring table) covers this layer uniformly.
    """


class DocumentRevisionConflict(FileManagementError):
    """Optimistic-concurrency conflict on document current content (SRS §C01, 409).

    Raised by ``DocumentCurrentContentRepository.set_current_content`` when the
    caller-supplied ``expected_revision`` does not match the row's current
    ``content_revision`` — i.e. another writer committed a newer content
    pointer between the caller's read and write.
    """

    code = "document_revision_conflict"

    def __init__(
        self,
        document_id: str,
        expected: int,
        actual: int,
        message: str = "",
    ) -> None:
        self.document_id = document_id
        self.expected = expected
        self.actual = actual
        msg = message or (
            f"document {document_id!r} content_revision conflict: "
            f"expected {expected}, actual {actual}"
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Frozen records (exchange types between service <-> repository)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StorageObjectRecord:
    """A row in ``asset_storage_objects`` (SRS §3.1A, §8.5).

    Carries the business identity ``id`` plus the physical location
    ``(provider, bucket, object_key, object_version_id)``. The Port
    (D-020) only addresses by location; the Repository owns the id mapping.
    """

    id: str
    provider: str
    bucket: str
    object_key: str
    object_version_id: str | None
    sha256: str
    size: int
    mime: str | None
    artifact_class: str
    state: str
    etag: str | None = None
    encryption: str | None = None
    retention_until: str | None = None
    created_at: str = ""
    last_verified_at: str | None = None


@dataclass(frozen=True)
class DocumentCurrentContent:
    """Current-content pointer on ``asset_documents`` (SRS §8.3, §9.1).

    The revision is the optimistic-concurrency version: each successful
    ``set_current_content`` increments it.
    """

    document_id: str
    storage_object_id: str
    source_raw_hash: str
    content_revision: int
    content_updated_at: str | None = None


@dataclass(frozen=True)
class DocumentRow:
    """Full row of ``asset_documents`` needed by the file-management service.

    Carries the directory-display fields (``kb_id`` / ``folder_id`` /
    ``document_name`` / ``deleted_at``) that the slim
    :class:`DocumentCurrentContent` pointer intentionally omits. M1.3
    ``FileManagementService`` reads/writes these for list / rename / move /
    soft-delete / restore (SRS §4.3A operation semantics table). Read
    permission enforcement stays in the API layer (``router``).
    """

    document_id: str
    kb_id: str
    folder_id: str | None
    document_name: str | None
    storage_object_id: str | None
    source_raw_hash: str | None
    content_revision: int
    deleted_at: str | None = None


# ---------------------------------------------------------------------------
# Repository Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class StorageObjectRepository(Protocol):
    """Repository for ``asset_storage_objects`` (SRS §3.1A, §8.5).

    Owns the business identity ``storage_object_id``. The dedup invariant
    (D-002 / O3): within one provider, one ``(bucket, object_key,
    object_version_id)`` maps to at most one record; ``find_by_location`` is
    the dedup probe used by the upload commit path.
    """

    async def register(self, record: StorageObjectRecord) -> StorageObjectRecord:
        """Insert a new storage object row; return the stored record."""
        ...

    async def get(self, storage_object_id: str) -> StorageObjectRecord | None:
        """Return the record for ``storage_object_id``, or None."""
        ...

    async def find_by_location(
        self,
        bucket: str,
        object_key: str,
        version_id: str | None = None,
    ) -> StorageObjectRecord | None:
        """Return the record at ``(bucket, object_key, version_id?)``, or None.

        The dedup probe (D-002): same sha256 → same object → reuse the record
        instead of writing a second object.
        """
        ...

    async def set_state(self, storage_object_id: str, state: str) -> None:
        """Update the storage object's lifecycle state (SRS §9.0B)."""
        ...

    async def mark_verified(self, storage_object_id: str, at: str) -> None:
        """Record that the object's bytes were integrity-verified at ``at``."""
        ...


@runtime_checkable
class DocumentCurrentContentRepository(Protocol):
    """Repository for the current-content pointer on ``asset_documents``.

    SRS §4.3 / §4.3A: a Logical Document has exactly one "current content"
    pointer (``storage_object_id`` + ``source_raw_hash`` + ``content_revision``)
    at a time. Replacing it is an optimistic-concurrency-guarded operation.
    """

    async def get(self, document_id: str) -> DocumentCurrentContent | None:
        """Return the current-content pointer, or None if the doc has none."""
        ...

    async def create_document(
        self,
        *,
        kb_id: str,
        document_id: str,
        folder_id: str | None,
        owner_id: str | None,
        domain: str | None = None,
        document_name: str | None,
        document_type: str | None,
        storage_object_id: str,
        source_raw_hash: str,
    ) -> DocumentCurrentContent:
        """Create a new Logical Document row pointing at ``storage_object_id``.

        The new row's ``content_revision`` is ``1`` (first content). Raises if
        a row with ``document_id`` already exists.

        ``domain``: 文档所属隔离域（SRS §A12）。传 ``None`` 时由实现自行解析——
        PG 实现查询 ``knowledge_bases``（KB 不存在/已删则拒绝，不产生幽灵文档）；
        memory 实现默认 ``generic``。关系链：全局用户 → domain（管理员经
        domain_registry.yaml 管理）→ 知识库（knowledge_bases.domain NOT NULL）→
        文档（asset_documents.domain 冗余自 KB，作隔离键）。
        """
        ...

    async def set_current_content(
        self,
        document_id: str,
        storage_object_id: str,
        raw_hash: str,
        *,
        expected_revision: int,
    ) -> DocumentCurrentContent:
        """Atomically advance the current-content pointer (SRS §4.3A).

        Optimistic concurrency: the row's ``content_revision`` MUST equal
        ``expected_revision``; on mismatch raises
        :class:`DocumentRevisionConflict`. On success the revision is
        incremented and ``content_updated_at`` set to now.
        """
        ...

    async def mark_outdated(self, document_id: str) -> None:
        """Flag that the current content is being re-parsed (lifecycle hint)."""
        ...

    # -- M1.3 directory-management methods (list/rename/move/soft_delete/restore)
    # These operate on the directory-display columns of ``asset_documents``
    # and never touch the bytes (SRS §4.3A). Added by M1.3 ``FileManagementService``.

    async def get_row(self, document_id: str) -> DocumentRow | None:
        """Return the full directory row for ``document_id``, or None.

        Unlike :meth:`get`, this returns the row even when the current-content
        pointer is NULL (e.g. freshly soft-deleted-but-restorable state).
        ``deleted_at`` is populated so the caller can distinguish soft-deleted
        rows.
        """
        ...

    async def list_in_kb(
        self,
        kb_id: str,
        *,
        folder_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[DocumentRow]:
        """List documents in a KB, optionally filtered by folder.

        Soft-deleted rows (``deleted_at IS NOT NULL``) are hidden unless
        ``include_deleted=True`` (SRS §4.3A).
        """
        ...

    async def rename(
        self, document_id: str, new_name: str
    ) -> DocumentRow:
        """Update ``document_name`` only (SRS §4.3A rename — no object change)."""
        ...

    async def move(
        self, document_id: str, target_folder_id: str | None
    ) -> DocumentRow:
        """Update ``folder_id`` only (SRS §4.3A move — no object change).

        ``target_folder_id=None`` moves the document to the KB root.
        """
        ...

    async def set_deleted(self, document_id: str) -> DocumentRow:
        """Soft-delete: stamp ``deleted_at = now`` (SRS §4.3A).

        Does NOT delete the storage object — still-referenced objects are only
        physically reclaimed by M1 GC (SRS §8.6).
        """
        ...

    async def clear_deleted(self, document_id: str) -> DocumentRow:
        """Restore: clear ``deleted_at`` (SRS §4.3A restore)."""
        ...


__all__ = [
    # errors
    "FileManagementError",
    "DocumentRevisionConflict",
    # records
    "StorageObjectRecord",
    "DocumentCurrentContent",
    "DocumentRow",
    # protocols
    "StorageObjectRepository",
    "DocumentCurrentContentRepository",
]
