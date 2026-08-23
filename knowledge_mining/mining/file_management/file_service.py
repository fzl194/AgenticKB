"""File Management orchestration service (M1.3; ADR-0003 D-023).

``FileManagementService`` is the directory / lifecycle layer above the M1.2
upload session service. It implements the SRS §4.3A operation-semantics table
for Logical Documents: list / get / download-url / replace-content (online
edit) / rename / move / soft-delete / restore / purge-request.

Migration-period coexistence (SRS §2.3, D-023):
- The legacy ``DocumentService`` (``kb/services/document_service.py``) writes
  ``storage_path`` and continues to serve the existing ``kb/routes/documents``
  surface. This service is a *new*, parallel link that writes
  ``storage_object_id`` over the Repository Protocol (M1.2). Nothing in this
  module imports or mutates ``DocumentService`` or the legacy routes; the
  upper layer switches traffic when ready.
- The service depends ONLY on the Repository Protocols
  (``contracts/file_management``) and ``ObjectStorePort``
  (``contracts/storage/port``). It has no psycopg / MinIO imports — the PG
  repos or the in-memory fakes are injected at construction, so the full test
  suite runs without PostgreSQL.

Operation semantics summary (SRS §4.3A):
  - rename / move / soft-delete / restore / purge-request: NO object change.
    Only the ``asset_documents`` directory row is mutated; the storage object
    is untouched (still-referenced objects are physically reclaimed only by
    M1 GC, SRS §8.6).
  - replace-content: copy-on-write — a new immutable object is written and the
    document's current-content pointer advances (optimistic concurrency). The
    previous object remains (referenced by revision history / builds).
  - purge-request: this service only *registers* the request + audit event;
    physical deletion is deferred to M1 GC (must check no active reference /
    Build reference / retention).

References:
- SRS §4.3A (operation semantics), §4.3 (current content), §8.6 (GC), §C01
  (error codes), §9.0B (MISSING/CORRUPT integrity incident).
- ADR-0003 D-002 (sha256 dedup), D-020 (location addressing), D-022
  (Repository Protocol + service layering), D-023 (this layer).
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone

from knowledge_mining.mining.contracts.file_management import (
    DocumentCurrentContentRepository,
    DocumentRevisionConflict,
    DocumentRow,
    FileAuditEvent,
    FileAuditRepository,
    QuotaRepository,
    StorageObjectRecord,
    StorageObjectRepository,
    UploadSessionRepository,
)
from knowledge_mining.mining.contracts.storage.errors import (
    StorageError,
    StorageObjectMissing,
)
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import (
    ObjectLocation,
    PresignedAccess,
    PutOptions,
)
from knowledge_mining.mining.infra.object_store.keys import build_object_key


# ---------------------------------------------------------------------------
# Errors (SRS §C01; API-layer maps these to HTTP)
# ---------------------------------------------------------------------------


class FileManagementServiceError(Exception):
    """Base class for FileManagementService business errors."""


class NotFound(FileManagementServiceError):
    """The document does not exist (SRS §C01 -> 404)."""


class Forbidden(FileManagementServiceError):
    """The actor lacks the required permission (SRS §C01 -> 403).

    Authorization is enforced by the API layer; this is a sentinel the router
    can raise when it performs the check and short-circuits.
    """


# ---------------------------------------------------------------------------
# View model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentView:
    """API-facing view of a Logical Document (SRS §4.3 / §4.3A).

    Combines the directory fields (``kb_id`` / ``folder_id`` / ``name`` /
    ``deleted_at``) with the current-content pointer
    (``storage_object_id`` / ``raw_hash`` / ``content_revision``) plus the
    storage object's ``size`` / ``mime`` resolved from the storage-objects
    registry. ``size`` / ``mime`` are ``None`` when the document has no
    current content (degenerate state).
    """

    document_id: str
    kb_id: str
    folder_id: str | None
    name: str | None
    mime: str | None
    size: int | None
    content_revision: int
    storage_object_id: str | None
    raw_hash: str | None
    deleted_at: str | None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileManagementServiceConfig:
    """Tuning knobs for ``FileManagementService``."""

    bucket_prefix: str = "agentickb-dev-"
    source_bucket: str | None = None  # defaults to {prefix}source
    presign_get_ttl_seconds: int = 900


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class FileManagementService:
    """Directory + lifecycle service for Logical Documents (SRS §4.3A).

    Construct with the object store + the five repositories + config. All
    methods are async. Read-permission enforcement is the caller's
    responsibility (the API router performs it before delegating here).
    """

    def __init__(
        self,
        *,
        object_store: ObjectStorePort,
        documents: DocumentCurrentContentRepository,
        storage_objects: StorageObjectRepository,
        audits: FileAuditRepository,
        quotas: QuotaRepository,
        sessions: UploadSessionRepository,
        config: FileManagementServiceConfig | None = None,
    ) -> None:
        self._store = object_store
        self._documents = documents
        self._storage_objects = storage_objects
        self._audits = audits
        self._quotas = quotas  # reserved for future purge-accounting; unused in M1.3 core paths
        self._sessions = sessions  # reserved for purge cross-check; unused in M1.3 core paths
        self._config = config or FileManagementServiceConfig()

    # -- helpers ---------------------------------------------------------

    def _source_bucket(self) -> str:
        return self._config.source_bucket or f"{self._config.bucket_prefix}source"

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    async def _resolve_view(self, row: DocumentRow) -> DocumentView:
        """Enrich a directory row with size/mime from the storage-objects registry."""
        size: int | None = None
        mime: str | None = None
        if row.storage_object_id is not None:
            obj = await self._storage_objects.get(row.storage_object_id)
            if obj is not None:
                size = obj.size
                mime = obj.mime
        return DocumentView(
            document_id=row.document_id,
            kb_id=row.kb_id,
            folder_id=row.folder_id,
            name=row.document_name,
            mime=mime,
            size=size,
            content_revision=row.content_revision,
            storage_object_id=row.storage_object_id,
            raw_hash=row.source_raw_hash,
            deleted_at=row.deleted_at,
        )

    async def _require_row(self, document_id: str) -> DocumentRow:
        row = await self._documents.get_row(document_id)
        if row is None:
            raise NotFound(f"document {document_id!r} not found")
        return row

    async def _audit(
        self,
        *,
        row: DocumentRow,
        actor: str,
        action: str,
        before: dict | None = None,
        after: dict | None = None,
        storage_object_id: str | None = None,
        content_revision: int | None = None,
    ) -> None:
        await self._audits.append(
            FileAuditEvent(
                id=self._new_id("audit"),
                kb_id=row.kb_id,
                document_id=row.document_id,
                storage_object_id=storage_object_id or row.storage_object_id,
                content_revision=content_revision
                if content_revision is not None
                else row.content_revision,
                actor=actor,
                action=action,
                before_json=before or {},
                after_json=after or {},
            )
        )

    # -- read paths ------------------------------------------------------

    async def list_documents(
        self,
        kb_id: str,
        *,
        folder_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[DocumentView]:
        """List documents in a KB (SRS §4.3A). Soft-deleted rows hidden by default."""
        rows = await self._documents.list_in_kb(
            kb_id, folder_id=folder_id, include_deleted=include_deleted
        )
        return [await self._resolve_view(r) for r in rows]

    async def get_document(self, document_id: str) -> DocumentView:
        """Return a single document (current storage_object/raw_hash/size/mime)."""
        return await self._resolve_view(await self._require_row(document_id))


    async def download_url(
        self,
        document_id: str,
        *,
        expires_seconds: int = 900,
    ) -> PresignedAccess:
        """Mint a short-lived GET URL for the document's current object (SRS §C00).

        Read authorization is enforced by the API layer before this call.
        Raises :class:`StorageObjectMissing` (-> 409) when the referenced
        object is MISSING/CORRUPT (SRS §9.0B) — this is never masked as 404.
        """
        row = await self._require_row(document_id)
        if row.storage_object_id is None:
            raise NotFound(
                f"document {document_id!r} has no current content to download"
            )
        obj = await self._storage_objects.get(row.storage_object_id)
        if obj is None:
            raise StorageObjectMissing(row.storage_object_id)
        location = ObjectLocation(
            bucket=obj.bucket, object_key=obj.object_key, version_id=obj.object_version_id
        )
        return await self._store.presign_get(location, expires_seconds)

    # -- content replacement (online edit) -------------------------------

    async def replace_content(
        self,
        document_id: str,
        *,
        stream: AsyncIterator[bytes],
        expected_revision: int,
        mime: str | None,
        actor: str,
    ) -> DocumentView:
        """Online content replace — copy-on-write (SRS §4.3A replace_content).

        Writes a new immutable object in the source bucket (content-addressed
        by sha256), registers an AVAILABLE StorageObject, advances the
        document's current-content pointer with optimistic concurrency, and
        appends a ``replace_content`` audit event. The previous object is NOT
        deleted (referenced by revision history / builds).
        """
        row = await self._require_row(document_id)
        before = {
            "storage_object_id": row.storage_object_id,
            "content_revision": row.content_revision,
        }

        storage_object = await self._write_new_object(stream=stream, mime=mime)
        try:
            updated = await self._documents.set_current_content(
                document_id,
                storage_object.id,
                storage_object.sha256,
                expected_revision=expected_revision,
            )
        except Exception:
            # Optimistic-concurrency conflict (DocumentRevisionConflict) or row
            # gone: leave the freshly-written object in place (it is immutable,
            # content-addressed, and reclaimable by GC). Re-raise as-is so the
            # router maps the exact error code.
            raise

        await self._documents.mark_outdated(document_id)
        after_row = await self._require_row(document_id)
        await self._audit(
            row=after_row,
            actor=actor,
            action="replace_content",
            before=before,
            after={
                "storage_object_id": storage_object.id,
                "sha256": storage_object.sha256,
                "size": storage_object.size,
                "content_revision": updated.content_revision,
            },
            storage_object_id=storage_object.id,
            content_revision=updated.content_revision,
        )
        return await self._resolve_view(after_row)

    async def _write_new_object(
        self,
        *,
        stream: AsyncIterator[bytes],
        mime: str | None,
    ) -> StorageObjectRecord:
        """put_stream a new content-addressed object + register StorageObject."""
        bucket = self._source_bucket()
        # Put to a temp key first to discover the sha256 (content addressing
        # requires the hash up-front; we cannot know the final key until the
        # bytes are hashed server-side).
        temp_key = self._new_id("replacetmp")
        temp_location = ObjectLocation(bucket=bucket, object_key=temp_key)
        put_options = PutOptions(
            artifact_class="source", mime=mime, content_length=None
        )
        put_result = await self._store.put_stream(temp_location, stream, put_options)
        sha256 = put_result.sha256
        size = put_result.size

        final_key = build_object_key("source", sha256)
        final_location = ObjectLocation(bucket=bucket, object_key=final_key)
        # Dedup: if an object already exists at the final content-addressed
        # location, reuse the StorageObject and skip the copy (D-002 / O3).
        existing = await self._storage_objects.find_by_location(bucket, final_key, None)
        if existing is not None:
            await self._safe_delete(temp_location)
            return existing
        # Copy temp -> final (no re-hash needed: copy preserves bytes).
        await self._store.copy(
            temp_location,
            final_location,
            PutOptions(
                artifact_class="source",
                mime=mime,
                expected_sha256=sha256,
                content_length=size,
            ),
        )
        # Best-effort cleanup of the temp object now that the copy succeeded.
        await self._safe_delete(temp_location)
        record = StorageObjectRecord(
            id=self._new_id("obj"),
            provider=self._provider_name(),
            bucket=bucket,
            object_key=final_key,
            object_version_id=None,
            sha256=sha256,
            size=size,
            mime=mime,
            artifact_class="source",
            state="AVAILABLE",
            etag=put_result.etag,
            created_at=self._utcnow(),
            last_verified_at=self._utcnow(),
        )
        registered = await self._storage_objects.register(record)
        return registered

    async def _safe_delete(self, location: ObjectLocation) -> None:
        try:
            await self._store.delete(location)
        except StorageError:
            # Best-effort temp cleanup; the temp object is reclaimable by GC.
            pass

    def _provider_name(self) -> str:
        return getattr(self._store, "provider", "unknown")

    # -- directory mutations (no object change) --------------------------

    async def rename(
        self, document_id: str, *, new_name: str, actor: str
    ) -> DocumentView:
        """Rename a document — directory row only (SRS §4.3A rename)."""
        before_row = await self._require_row(document_id)
        before = {"document_name": before_row.document_name}
        row = await self._documents.rename(document_id, new_name)
        await self._audit(
            row=row, actor=actor, action="rename",
            before=before, after={"document_name": new_name},
        )
        return await self._resolve_view(row)

    async def move(
        self, document_id: str, *, target_folder_id: str | None, actor: str
    ) -> DocumentView:
        """Move a document — directory row only (SRS §4.3A move)."""
        before_row = await self._require_row(document_id)
        before = {"folder_id": before_row.folder_id}
        row = await self._documents.move(document_id, target_folder_id)
        await self._audit(
            row=row, actor=actor, action="move",
            before=before, after={"folder_id": target_folder_id},
        )
        return await self._resolve_view(row)

    async def soft_delete(self, document_id: str, *, actor: str) -> None:
        """Soft-delete — stamp deleted_at, keep the object (SRS §4.3A, §8.6)."""
        row = await self._require_row(document_id)
        before = {"deleted_at": row.deleted_at}
        updated = await self._documents.set_deleted(document_id)
        await self._audit(
            row=updated, actor=actor, action="soft_delete",
            before=before, after={"deleted_at": updated.deleted_at},
        )

    async def restore(self, document_id: str, *, actor: str) -> DocumentView:
        """Restore a soft-deleted document — clear deleted_at (SRS §4.3A)."""
        row = await self._require_row(document_id)
        before = {"deleted_at": row.deleted_at}
        updated = await self._documents.clear_deleted(document_id)
        await self._audit(
            row=updated, actor=actor, action="restore",
            before=before, after={"deleted_at": None},
        )
        return await self._resolve_view(updated)

    async def purge(self, document_id: str, *, actor: str) -> None:
        """Register a purge request + audit (SRS §8.6). Does NOT delete bytes.

        Physical deletion is deferred to M1 GC, which must first check there
        is no active reference, Build reference, or retention hold on the
        object. M1.3 only records intent.
        """
        row = await self._require_row(document_id)
        await self._audit(
            row=row,
            actor=actor,
            action="purge_request",
            before={},
            after={"requested_at": self._utcnow()},
            storage_object_id=row.storage_object_id,
            content_revision=None,
        )


__all__ = [
    "DocumentView",
    "FileManagementService",
    "FileManagementServiceConfig",
    "FileManagementServiceError",
    "Forbidden",
    "NotFound",
]
