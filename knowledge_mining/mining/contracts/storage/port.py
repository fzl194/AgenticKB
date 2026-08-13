"""Protocol interfaces for the Object Store contract (WP0.3, revised D-020 M1).

``ObjectStorePort`` is the single seam between business/parse layers and the
object store. It is fully async (the business layer is async); the production
``MinioObjectStore`` adapter wraps the synchronous MinIO SDK via
``asyncio.to_thread`` (ADR-0003 D-002) and lands in M1 (WP1A).

The Port exposes ONLY project types (``ObjectLocation``, ``ObjectStat``,
``UploadTicket``, ``PresignedAccess`` ...) and project error codes — never
MinIO SDK types, ``S3Error``, bucket naming, or long-term credentials
(SRS §C00).

Addressing model (ADR-0003 D-020, supersedes D-013 #1):
  - Every byte operation is addressed by ``ObjectLocation(bucket, object_key,
    version_id?)`` — the native S3/MinIO model. The project business identity
    ``storage_object_id`` is owned by the Repository (M1.2 / WP1B) and backed
    by the PG ``asset_storage_objects`` registry; the Port does not know it.
  - Callers (Repository) choose the ``object_key`` per SRS §3.1A / §8.1 (system
    key, no business semantics). ``put_stream`` therefore takes
    ``(location, stream, options)``.
  - ``SourceArtifactReader`` was removed (D-020): it addressed by
    ``storage_object_id``, which is an application-layer concern. M1.2
    implements it as ``Repository.resolve(id) -> ObjectLocation`` composed with
    ``ObjectStore.get_stream(location)``.

References:
- SRS §C00 (MinIO Object Storage Foundation)
- SRS §3.1A / §3.1B (Storage Object / Upload Session)
- SRS §8.1 (bucket / object_key strategy)
- SRS §9.5 (recovery)
- ADR-0003 D-002 (dual adapter), D-007 (signature tradeoffs), D-020 (location)
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from knowledge_mining.mining.contracts.storage.types import (
    ObjectLocation,
    ObjectStat,
    PartETag,
    PresignedAccess,
    PutOptions,
    PutResult,
    UploadTicket,
)


@runtime_checkable
class ObjectStorePort(Protocol):
    """Async port for reading/writing immutable objects.

    All byte operations are addressed by ``ObjectLocation`` (D-020). The caller
    (Repository) selects ``(bucket, object_key)`` per SRS §3.1A / §8.1; the
    adapter never invents the location.
    """

    async def put_stream(
        self,
        location: ObjectLocation,
        stream: AsyncIterator[bytes],
        options: PutOptions,
    ) -> PutResult:
        """Stream-upload bytes to ``location``; compute SHA-256 server-side.

        If ``options.expected_sha256`` is set and disagrees with the computed
        hash, raise ``ChecksumMismatch`` (SRS §C01) and leave no object behind.
        Returns ``PutResult`` carrying the computed ``sha256`` / ``size`` plus
        the store-assigned ``version_id`` / ``etag``.
        """
        ...

    async def get_stream(self, location: ObjectLocation) -> AsyncIterator[bytes]:
        """Return an async byte stream of the object's contents at ``location``.

        Raises ``StorageObjectMissing`` if the object does not exist.
        """
        ...

    async def stat(self, location: ObjectLocation) -> ObjectStat:
        """Return object metadata without fetching bytes (SRS §3.1A)."""
        ...

    async def delete(self, location: ObjectLocation) -> None:
        """Delete the object at ``location``. Soft vs physical delete is an adapter decision."""
        ...

    async def head_exists(self, location: ObjectLocation) -> bool:
        """Cheap existence probe (HEAD semantics)."""
        ...

    async def copy(
        self,
        src: ObjectLocation,
        dst: ObjectLocation,
        options: PutOptions,
    ) -> PutResult:
        """Server-side copy producing a new object at ``dst`` with the same bytes."""
        ...

    async def presign_get(
        self,
        location: ObjectLocation,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        """Mint a short-lived GET URL for an existing object (SRS §C00)."""
        ...

    async def presign_put(
        self,
        location: ObjectLocation,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        """Mint a short-lived PUT URL for a not-yet-existing object (SRS §C00)."""
        ...

    # -- Multipart (SRS §3.1B, §C00) --------------------------------------

    async def initiate_multipart(
        self,
        location: ObjectLocation,
        options: PutOptions,
    ) -> UploadTicket:
        """Begin a resumable multipart upload targeting ``location``.

        The object is not materialized until ``complete_multipart`` succeeds.
        """
        ...

    async def upload_part(
        self,
        upload_id: str,
        part_number: int,
        stream: AsyncIterator[bytes],
    ) -> PartETag:
        """Upload one part; return its ETag for later completion."""
        ...

    async def complete_multipart(
        self,
        upload_id: str,
        parts: tuple[PartETag, ...],
        expected_sha256: str | None = None,
    ) -> PutResult:
        """Finalize a multipart upload, assembling the object at its location.

        Computes the composite SHA-256 over the assembled bytes; if
        ``expected_sha256`` is set and disagrees, raises ``ChecksumMismatch``
        and discards the assembled object.
        """
        ...

    async def abort_multipart(self, upload_id: str) -> None:
        """Abort a multipart upload and discard its parts (SRS §9.5)."""
        ...


__all__ = ["ObjectStorePort"]
