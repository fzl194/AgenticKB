"""Protocol interfaces for the Object Store contract (WP0.3).

``ObjectStorePort`` is the single seam between business/parse layers and the
object store. It is fully async (the business layer is async); the production
``MinioObjectStore`` adapter wraps the synchronous MinIO SDK via
``asyncio.to_thread`` (ADR-0003 D-002) and lands in M1 (WP1A).

The Port exposes ONLY project types (``ObjectRef``, ``ObjectStat``,
``UploadTicket``, ``PresignedAccess`` ...) and project error codes — never
MinIO SDK types, ``S3Error``, bucket naming, or long-term credentials
(SRS §C00).

References:
- SRS §C00 (MinIO Object Storage Foundation)
- SRS §3.1A / §3.1B (Storage Object / Upload Session)
- SRS §9.5 (recovery), §4.1A (SourceArtifactReader consumers)
- ADR-0003 D-002 (dual adapter), D-007 (signature tradeoffs)
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from knowledge_mining.mining.contracts.storage.types import (
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

    Identity model (SRS §3.1A):
      - ``storage_object_id`` is the business identity (a string). Every method
        that operates on an *existing* object takes it.
      - ``bucket`` / ``object_key`` appear ONLY on ``presign_put`` and
        ``initiate_multipart``, where the object does not yet exist.
      - The adapter owns ``object_key`` generation (system-generated, immutable,
        no business semantics — SRS §3.1A). Callers never supply a key for the
        normal put path; ``put_stream`` therefore takes ``(stream, options)``
        and returns the newly assigned ``storage_object_id``.
    """

    async def put_stream(
        self,
        stream: AsyncIterator[bytes],
        options: PutOptions,
    ) -> PutResult:
        """Stream-upload bytes; compute SHA-256 server-side.

        If ``options.expected_sha256`` is set and disagrees with the computed
        hash, raise ``ChecksumMismatch`` (SRS §C01). The adapter assigns the
        ``storage_object_id``, ``bucket`` and ``object_key``.
        """
        ...

    async def get_stream(self, storage_object_id: str) -> AsyncIterator[bytes]:
        """Return an async byte stream of the object's contents.

        Raises ``StorageObjectMissing`` if the object does not exist.
        """
        ...

    async def stat(self, storage_object_id: str) -> ObjectStat:
        """Return object metadata without fetching bytes (SRS §3.1A)."""
        ...

    async def delete(self, storage_object_id: str) -> None:
        """Delete the object. Soft vs physical delete is an adapter decision."""
        ...

    async def head_exists(self, storage_object_id: str) -> bool:
        """Cheap existence probe (HEAD semantics)."""
        ...

    async def copy(
        self,
        src_storage_object_id: str,
        dst_options: PutOptions,
    ) -> PutResult:
        """Server-side copy producing a new Storage Object with the same bytes."""
        ...

    async def presign_get(
        self,
        storage_object_id: str,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        """Mint a short-lived GET URL for an existing object (SRS §C00)."""
        ...

    async def presign_put(
        self,
        bucket: str,
        object_key: str,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        """Mint a short-lived PUT URL for a not-yet-existing object (SRS §C00)."""
        ...

    # -- Multipart (SRS §3.1B, §C00) --------------------------------------

    async def initiate_multipart(
        self,
        bucket: str,
        object_key: str,
        options: PutOptions,
    ) -> UploadTicket:
        """Begin a resumable multipart upload. Object is not yet materialized."""
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
        """Finalize a multipart upload, assembling the object.

        Computes the composite SHA-256 over the assembled bytes; if
        ``expected_sha256`` is set and disagrees, raises ``ChecksumMismatch``
        and discards the assembled object.
        """
        ...

    async def abort_multipart(self, upload_id: str) -> None:
        """Abort a multipart upload and discard its parts (SRS §9.5)."""
        ...


@runtime_checkable
class SourceArtifactReader(Protocol):
    """Convenience facade built on top of ``ObjectStorePort`` (SRS §C00).

    Inspector / Parser adapters consume source artifacts through this interface
    so they never touch ``ObjectStorePort`` (or MinIO) directly. It guarantees
    run-scoped materialization with hash verification (SRS §C02 frozen input,
    §C03 inspector, WP1D).
    """

    async def open_stream(self, storage_object_id: str) -> AsyncIterator[bytes]:
        """Open a streaming read over the frozen source object."""
        ...

    async def materialize_temp(
        self,
        storage_object_id: str,
        run_id: str,
    ) -> Path:
        """Materialize the object to a run-scoped temp file and verify its hash.

        The returned path is valid for the lifetime of ``run_id``. Raises
        ``ChecksumMismatch`` if the materialized bytes drift from the recorded
        hash (tamper / bit-rot detection, SRS §9.5).
        """
        ...


__all__ = ["ObjectStorePort", "SourceArtifactReader"]
