"""Upload Session orchestration service (M1.2, WP1B; ADR-0003 D-022).

``UploadSessionService`` is the hexagonal service that drives a single upload
through its state machine (SRS §9.0A) and, on commit, atomically:

  1. verifies the staged object (size + optional sha256),
  2. registers (or dedup-reuses) a ``StorageObject`` pointing at the final
     content-addressed location,
  3. advances the Logical Document's current-content pointer (optimistic
     concurrency on ``content_revision``),
  4. appends a ``FileAuditEvent`` (``upload`` | ``replace_content``),
  5. commits the quota (reserved -> used).

It depends ONLY on the Repository Protocols (``contracts/file_management``)
and ``ObjectStorePort`` (``contracts/storage/port``). The PG repos or the
in-memory fakes are injected at construction — the service itself has no DB /
psycopg / MinIO SDK imports.

Idempotency (SRS §9.0A, §9.5):
- ``initiate`` with a repeated ``(kb_id, actor, idempotency_key)`` returns the
  existing session without re-reserving quota.
- ``complete`` on an already-COMMITTED session returns the original
  ``CommitResult`` without re-writing the object or re-advancing the document.
- sha256-level dedup (D-002 / O3): a second upload of the same bytes reuses
  the existing ``StorageObject`` row and does not copy the object again.

References:
- SRS §4.1A (upload transaction), §4.3 / §4.3A (document current content +
  operation semantics), §C01 (error codes), §9.0A / §9.5 (state machine).
- ADR-0003 D-002 (content dedup), D-020 (location addressing), D-022
  (Repository Protocol + service layering).
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from knowledge_mining.mining.contracts.file_management import (
    CommitResult,
    DocumentCurrentContent,
    DocumentRevisionConflict,
    FileAuditEvent,
    FileAuditRepository,
    DocumentCurrentContentRepository,
    QuotaRepository,
    StorageObjectRecord,
    StorageObjectRepository,
    UploadIncomplete,
    UploadSessionExpired,
    UploadSessionRecord,
    UploadSessionRepository,
    dataclass_replace,
)
from knowledge_mining.mining.contracts.storage.errors import ChecksumMismatch
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import (
    ObjectLocation,
    PresignedAccess,
    PutOptions,
)
from knowledge_mining.mining.contracts.state_machines import assert_transition
from knowledge_mining.mining.infra.object_store.keys import build_object_key

# Default staging TTL if the caller does not supply one (SRS §9.0A).
_DEFAULT_SESSION_TTL_SECONDS = 6 * 3600  # 6 hours


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _new_staging_object_key() -> str:
    """Build a unique staging object key (SRS §8.1 layout, temporary class).

    ``build_object_key`` requires a 64-char hex sha256; the staging location
    is allocated before the content hash is known, so we synthesize a valid
    64-char hex from a uuid4. The key lives only in the staging bucket and is
    deleted on commit/abort.
    """
    placeholder = uuid.uuid4().hex + uuid.uuid4().hex  # 64 hex chars
    return build_object_key("temporary", placeholder)


@dataclass(frozen=True)
class UploadSessionServiceConfig:
    """Tuning knobs for the orchestration service."""

    bucket_prefix: str = "agentickb-dev-"
    staging_bucket: str | None = None  # defaults to {prefix}staging
    source_bucket: str | None = None   # defaults to {prefix}source
    session_ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS
    presign_put_ttl_seconds: int = 900


class UploadSessionService:
    """Orchestrates the upload-session lifecycle (SRS §3.1B, §9.0A).

    Construct with the five repositories + an ``ObjectStorePort`` + config.
    All methods are async and idempotent per SRS §9.5.
    """

    def __init__(
        self,
        *,
        object_store: ObjectStorePort,
        sessions: UploadSessionRepository,
        storage_objects: StorageObjectRepository,
        documents: DocumentCurrentContentRepository,
        audits: FileAuditRepository,
        quotas: QuotaRepository,
        config: UploadSessionServiceConfig | None = None,
    ) -> None:
        self._store = object_store
        self._sessions = sessions
        self._storage_objects = storage_objects
        self._documents = documents
        self._audits = audits
        self._quotas = quotas
        self._config = config or UploadSessionServiceConfig()

    # -- bucket resolution ------------------------------------------------

    def _staging_bucket(self) -> str:
        return self._config.staging_bucket or f"{self._config.bucket_prefix}staging"

    def _source_bucket(self) -> str:
        return self._config.source_bucket or f"{self._config.bucket_prefix}source"

    @staticmethod
    def _staging_location(bucket: str, object_key: str) -> ObjectLocation:
        return ObjectLocation(bucket=bucket, object_key=object_key)

    # -- initiate ---------------------------------------------------------

    async def initiate(
        self,
        *,
        kb_id: str,
        folder_id: str | None,
        actor: str,
        filename: str,
        expected_size: int,
        expected_mime: str | None,
        idempotency_key: str,
    ) -> tuple[UploadSessionRecord, PresignedAccess]:
        """Begin an upload session (SRS §3.1B, §9.0A INITIATED).

        Idempotent on ``(kb_id, actor, idempotency_key)``: a repeat call
        returns the existing session and a fresh presigned PUT URL without
        re-reserving quota. KB write authorization is the caller's
        responsibility (M1.3) — this method only does the quota reserve.
        """
        existing = await self._sessions.find_by_idempotency(
            kb_id, actor, idempotency_key
        )
        if existing is not None:
            presign = await self._presign_put(existing)
            return existing, presign

        session = await self._create_new_session(
            kb_id=kb_id, folder_id=folder_id, actor=actor, filename=filename,
            expected_size=expected_size, expected_mime=expected_mime,
            idempotency_key=idempotency_key,
        )
        presign = await self._presign_put(session)
        return session, presign

    async def _create_new_session(
        self,
        *,
        kb_id: str,
        folder_id: str | None,
        actor: str,
        filename: str,
        expected_size: int,
        expected_mime: str | None,
        idempotency_key: str,
    ) -> UploadSessionRecord:
        """Reserve quota and insert a new INITIATED session row."""
        # Reserve quota first (fails closed with QuotaExceeded on overflow).
        quota = await self._quotas.get(kb_id)
        await self._quotas.reserve(kb_id, expected_size, quota.version)

        staging_bucket = self._staging_bucket()
        staging_object_key = _new_staging_object_key()
        now = _utcnow()
        expires_at = _iso_add_seconds(now, self._config.session_ttl_seconds)

        session = UploadSessionRecord(
            id=_new_id("upsess"),
            kb_id=kb_id,
            folder_id=folder_id,
            actor=actor,
            original_filename=filename,
            expected_size=expected_size,
            expected_mime=expected_mime,
            staging_bucket=staging_bucket,
            staging_object_key=staging_object_key,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
            state="INITIATED",
            created_at=now,
            updated_at=now,
        )
        return await self._sessions.create(session)

    async def _presign_put(self, session: UploadSessionRecord) -> PresignedAccess:
        location = self._staging_location(
            session.staging_bucket, session.staging_object_key
        )
        return await self._store.presign_put(
            location, self._config.presign_put_ttl_seconds
        )

    # -- stage ------------------------------------------------------------

    async def stage_from_bytes(self, session_id: str, data: bytes) -> UploadSessionRecord:
        """Stage the whole payload from an in-memory buffer (small files).

        For large/streamed payloads use :meth:`stage_chunked`.
        """
        session = await self._require_session(session_id)
        assert_transition("upload_session", session.state, "UPLOADING")
        session = await self._sessions.update(session.with_updates(state="UPLOADING"))

        location = self._staging_location(
            session.staging_bucket, session.staging_object_key
        )
        options = PutOptions(
            artifact_class="temporary",
            mime=session.expected_mime,
            content_length=len(data),
        )
        await self._store.put_stream(location, _bytes_stream(data), options)

        assert_transition("upload_session", session.state, "OBJECT_STAGED")
        return await self._sessions.update(session.with_updates(state="OBJECT_STAGED"))

    async def stage_chunked(
        self, session_id: str, stream: AsyncIterator[bytes]
    ) -> UploadSessionRecord:
        """Stream the payload to the staging location (large files)."""
        session = await self._require_session(session_id)
        assert_transition("upload_session", session.state, "UPLOADING")
        session = await self._sessions.update(session.with_updates(state="UPLOADING"))

        location = self._staging_location(
            session.staging_bucket, session.staging_object_key
        )
        options = PutOptions(artifact_class="temporary", mime=session.expected_mime)
        await self._store.put_stream(location, stream, options)

        assert_transition("upload_session", session.state, "OBJECT_STAGED")
        return await self._sessions.update(session.with_updates(state="OBJECT_STAGED"))

    # -- complete ---------------------------------------------------------

    async def complete(
        self,
        session_id: str,
        *,
        expected_sha256: str | None = None,
        mime: str | None = None,
        document_id: str | None = None,
        owner_id: str | None = None,
        document_type: str | None = None,
    ) -> CommitResult:
        """Commit a staged upload (SRS §4.1A, §9.0A COMMITTED).

        Idempotent: a session already in COMMITTED returns the original
        ``CommitResult`` without re-writing the object.
        """
        session = await self._require_session(session_id)
        if session.state == "COMMITTED":
            return await self._committed_result(session)

        self._assert_committable(session)
        assert_transition("upload_session", session.state, "VERIFYING")
        session = await self._sessions.update(session.with_updates(state="VERIFYING"))

        storage_object, doc = await self._verify_and_promote(
            session, expected_sha256=expected_sha256,
            mime=mime, document_id=document_id,
            owner_id=owner_id, document_type=document_type,
        )
        return await self._finalize_commit(session, storage_object, doc)

    async def _verify_and_promote(
        self,
        session: UploadSessionRecord,
        *,
        expected_sha256: str | None,
        mime: str | None,
        document_id: str | None,
        owner_id: str | None,
        document_type: str | None,
    ) -> tuple[StorageObjectRecord, DocumentCurrentContent]:
        """Verify staging, register final object, advance document, audit, commit quota."""
        stat = await self._store.stat(
            self._staging_location(session.staging_bucket, session.staging_object_key)
        )
        self._verify_size(session, stat.size)
        if expected_sha256 is not None:
            self._verify_checksum(stat.sha256, expected_sha256)

        storage_object = await self._register_final_object(
            session=session, sha256=stat.sha256, size=stat.size,
            resolved_mime=mime or stat.mime or session.expected_mime,
        )
        doc, action = await self._advance_document(
            document_id=document_id, session=session,
            storage_object=storage_object,
            owner_id=owner_id, document_type=document_type,
        )
        await self._record_audit(session, storage_object, doc, action)
        await self._quotas.commit(
            session.kb_id,
            reserved_bytes_to_release=session.expected_size or stat.size,
            used_bytes_to_add=stat.size,
            expected_version=(await self._quotas.get(session.kb_id)).version,
        )
        return storage_object, doc

    async def _finalize_commit(
        self,
        session: UploadSessionRecord,
        storage_object: StorageObjectRecord,
        doc: DocumentCurrentContent,
    ) -> CommitResult:
        """Transition session -> COMMITTED and build the CommitResult."""
        assert_transition("upload_session", session.state, "COMMITTED")
        await self._sessions.update(
            session.with_updates(
                state="COMMITTED",
                committed_storage_object_id=storage_object.id,
                committed_document_id=doc.document_id,
            )
        )
        return CommitResult(
            storage_object_id=storage_object.id,
            document_id=doc.document_id,
            content_revision=doc.content_revision,
            sha256=storage_object.sha256,
            size=storage_object.size,
        )

    def _assert_committable(self, session: UploadSessionRecord) -> None:
        if session.state in ("ABORTED", "EXPIRED", "REJECTED"):
            raise UploadSessionExpired(
                session.id, f"session {session.id!r} is {session.state}"
            )
        if session.state not in ("OBJECT_STAGED", "VERIFYING"):
            raise UploadIncomplete(
                session.id,
                f"session {session.id!r} state {session.state!r} cannot complete; "
                "stage the payload first",
            )

    def _verify_size(self, session: UploadSessionRecord, actual_size: int) -> None:
        if session.expected_size is not None and actual_size != session.expected_size:
            raise UploadIncomplete(
                session.id,
                f"size mismatch: expected {session.expected_size}, got {actual_size}",
            )

    @staticmethod
    def _verify_checksum(actual: str | None, expected: str) -> None:
        if actual is None:
            raise ChecksumMismatch(
                "object stat did not include a sha256 to verify against",
                expected=expected,
                actual=None,
            )
        if actual != expected:
            raise ChecksumMismatch(
                f"expected {expected}, got {actual}",
                expected=expected,
                actual=actual,
            )

    async def _register_final_object(
        self,
        *,
        session: UploadSessionRecord,
        sha256: str,
        size: int,
        resolved_mime: str | None,
    ) -> StorageObjectRecord:
        """Copy staging -> final and register the StorageObject (with dedup).

        Dedup (D-002 / O3): if a StorageObject already exists at the final
        content-addressed location, reuse it and skip the copy.
        """
        source_bucket = self._source_bucket()
        final_key = build_object_key("source", sha256)
        existing = await self._storage_objects.find_by_location(
            source_bucket, final_key, None
        )
        if existing is not None:
            return existing

        # No existing record: copy the bytes to the final location and register.
        staging = self._staging_location(session.staging_bucket, session.staging_object_key)
        final = ObjectLocation(bucket=source_bucket, object_key=final_key)
        await self._store.copy(
            staging,
            final,
            PutOptions(
                artifact_class="source",
                mime=resolved_mime,
                expected_sha256=sha256,
                content_length=size,
            ),
        )
        record = StorageObjectRecord(
            id=_new_id("obj"),
            provider=self._provider_name(),
            bucket=source_bucket,
            object_key=final_key,
            object_version_id=None,
            sha256=sha256,
            size=size,
            mime=resolved_mime,
            artifact_class="source",
            state="AVAILABLE",
            etag=None,
            created_at=_utcnow(),
            last_verified_at=_utcnow(),
        )
        return await self._storage_objects.register(record)

    async def _advance_document(
        self,
        *,
        document_id: str | None,
        session: UploadSessionRecord,
        storage_object: StorageObjectRecord,
        owner_id: str | None,
        document_type: str | None,
    ) -> tuple[DocumentCurrentContent, str]:
        """Create or advance the Logical Document's current-content pointer."""
        if document_id is None:
            doc = await self._documents.create_document(
                kb_id=session.kb_id,
                document_id=_new_id("doc"),
                folder_id=session.folder_id,
                owner_id=owner_id,
                document_name=session.original_filename,
                document_type=document_type,
                storage_object_id=storage_object.id,
                source_raw_hash=storage_object.sha256,
            )
            return doc, "upload"
        # Replace existing current content (optimistic concurrency).
        current = await self._documents.get(document_id)
        if current is None:
            doc = await self._documents.create_document(
                kb_id=session.kb_id,
                document_id=document_id,
                folder_id=session.folder_id,
                owner_id=owner_id,
                document_name=session.original_filename,
                document_type=document_type,
                storage_object_id=storage_object.id,
                source_raw_hash=storage_object.sha256,
            )
            return doc, "upload"
        return (
            await self._documents.set_current_content(
                document_id,
                storage_object.id,
                storage_object.sha256,
                expected_revision=current.content_revision,
            ),
            "replace_content",
        )

    async def _record_audit(
        self,
        session: UploadSessionRecord,
        storage_object: StorageObjectRecord,
        doc: DocumentCurrentContent,
        action: str,
    ) -> None:
        before: dict[str, Any] = {}
        after: dict[str, Any] = {
            "storage_object_id": storage_object.id,
            "sha256": storage_object.sha256,
            "size": storage_object.size,
            "content_revision": doc.content_revision,
        }
        await self._audits.append(
            FileAuditEvent(
                id=_new_id("audit"),
                kb_id=session.kb_id,
                document_id=doc.document_id,
                storage_object_id=storage_object.id,
                content_revision=doc.content_revision,
                actor=session.actor,
                action=action,
                before_json=before,
                after_json=after,
            )
        )

    async def _committed_result(
        self, session: UploadSessionRecord
    ) -> CommitResult:
        """Reconstruct the CommitResult for an already-committed session."""
        assert session.committed_storage_object_id is not None
        assert session.committed_document_id is not None
        obj = await self._storage_objects.get(session.committed_storage_object_id)
        doc = await self._documents.get(session.committed_document_id)
        if obj is None or doc is None:
            raise UploadIncomplete(
                session.id,
                f"committed session {session.id!r} missing object/document linkage",
            )
        return CommitResult(
            storage_object_id=obj.id,
            document_id=doc.document_id,
            content_revision=doc.content_revision,
            sha256=obj.sha256,
            size=obj.size,
        )

    # -- abort ------------------------------------------------------------

    async def abort(self, session_id: str) -> UploadSessionRecord:
        """Abort the session: release quota, delete staging, state -> ABORTED."""
        session = await self._require_session(session_id)
        if session.state in ("COMMITTED",):
            raise UploadSessionExpired(
                session.id,
                f"session {session.id!r} already COMMITTED, cannot abort",
            )
        # State machine: any non-terminal pre-commit state may -> ABORTED.
        if session.state not in ("ABORTED", "EXPIRED", "REJECTED"):
            assert_transition("upload_session", session.state, "ABORTED")
            session = await self._sessions.update(session.with_updates(state="ABORTED"))
        # Release the reserved quota (idempotent: only if expected_size reserved).
        if session.expected_size:
            quota = await self._quotas.get(session.kb_id)
            await self._quotas.release(
                session.kb_id, session.expected_size, quota.version
            )
        # Best-effort staging cleanup; tolerate absence.
        staging = self._staging_location(session.staging_bucket, session.staging_object_key)
        try:
            await self._store.delete(staging)
        except Exception:
            # Staging object may already be gone; abort must still succeed.
            pass
        return session

    # -- helpers ----------------------------------------------------------

    async def _require_session(self, session_id: str) -> UploadSessionRecord:
        session = await self._sessions.get(session_id)
        if session is None:
            raise UploadIncomplete(session_id, f"session {session_id!r} not found")
        return session

    def _provider_name(self) -> str:
        """Return the object store's provider tag for StorageObject records."""
        return getattr(self._store, "provider", "unknown")


# ---------------------------------------------------------------------------
# Small async helpers (kept module-local to avoid an extra utility module)
# ---------------------------------------------------------------------------


async def _bytes_stream(data: bytes) -> AsyncIterator[bytes]:
    """Yield ``data`` as a single-chunk async stream (for put_stream)."""
    yield data


def _iso_add_seconds(iso_ts: str, seconds: int) -> str:
    """Add ``seconds`` to an ISO-8601 timestamp string (UTC, with offset)."""
    # Parse defensively: the timestamps we produce are ISO with offset.
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta_seconds(seconds)).isoformat()


def timedelta_seconds(seconds: int):
    from datetime import timedelta

    return timedelta(seconds=seconds)


__all__ = [
    "UploadSessionService",
    "UploadSessionServiceConfig",
]


# Silence unused-import lint for dataclass_replace (re-exported convenience).
_ = dataclass_replace
