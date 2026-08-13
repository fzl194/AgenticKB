"""Error catalogue for the Object Store contract (WP0.3).

Stable error codes are the contract the business/API layer maps to HTTP status.
Adapter implementations MUST normalize SDK exceptions (S3Error, MinioException,
network errors) into these project types — raw SDK errors never cross the
adapter boundary (SRS §C00, ADR-0003 D-002).

Reference table (SRS §C01 + §9.5):

    error code                       | HTTP  | meaning
    ---------------------------------|-------|-----------------------------------
    upload_session_expired           | 410   | session expired, re-initiate
    upload_incomplete                | 409   | parts/object incomplete
    file_too_large / quota_exceeded  | 413   | per-file or KB quota exceeded
    checksum_mismatch                | 422   | declared content != object bytes
    unsafe_file / archive_limit_...  | 422   | security admission failed
    storage_unavailable              | 503   | MinIO/network transient, retryable
    storage_object_missing           | 409   | catalog row exists, object missing
    storage_object_corrupt           | 409   | catalog row exists, object corrupt
    document_revision_conflict       | 409   | optimistic concurrency conflict
    storage_forbidden                | 403   | access denied at object store

This module defines the storage-layer subset (storage_*, checksum_mismatch,
quota_exceeded, storage_forbidden). The file-management subset
(upload_session_*, file_too_large, unsafe_file, document_revision_conflict)
is raised at the WP1B orchestration layer and is not duplicated here.
"""
from __future__ import annotations


class StorageError(Exception):
    """Base class for all Object Store errors.

    Subclasses set a stable ``code`` string used for metrics, logging and HTTP
    mapping. The ``code`` is the contract — do not change it without a migration
    plan.
    """

    code: str = "storage_error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            # Allow per-instance override without losing the class default.
            self.code = code


# ---------------------------------------------------------------------------
# Transient infrastructure failures (retryable)
# ---------------------------------------------------------------------------


class StorageUnavailable(StorageError):
    """MinIO / network briefly unavailable (SRS §C01, §9.5).

    Callers SHOULD retry with backoff. MUST NOT be masked as a
    ``StorageObjectMissing`` / 404 (SRS §9.5).
    """

    code = "storage_unavailable"


# ---------------------------------------------------------------------------
# Integrity incidents (SRS §9.0B: MISSING / CORRUPT)
# ---------------------------------------------------------------------------


class StorageObjectMissing(StorageError):
    """Catalog references an object whose bytes cannot be located.

    Distinct from a business-level 404: the directory row exists but the
    object is gone (SRS §9.0B -> MISSING).
    """

    code = "storage_object_missing"

    def __init__(self, storage_object_id: str, message: str = "") -> None:
        self.storage_object_id = storage_object_id
        msg = message or f"storage object missing: {storage_object_id}"
        super().__init__(msg)


class StorageObjectCorrupt(StorageError):
    """Object bytes fail integrity verification (SRS §9.0B -> CORRUPT)."""

    code = "storage_object_corrupt"

    def __init__(self, storage_object_id: str, message: str = "") -> None:
        self.storage_object_id = storage_object_id
        msg = message or f"storage object corrupt: {storage_object_id}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Content validation
# ---------------------------------------------------------------------------


class ChecksumMismatch(StorageError):
    """Declared content hash does not match the object bytes (SRS §C01, 422).

    Raised by ``put_stream`` / ``complete_multipart`` when
    ``expected_sha256`` is provided and disagrees with the computed hash, and
    by post-download verification.
    """

    code = "checksum_mismatch"

    def __init__(
        self,
        message: str = "",
        *,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(message or "checksum mismatch")


# ---------------------------------------------------------------------------
# Authorization & quota
# ---------------------------------------------------------------------------


class StorageForbidden(StorageError):
    """Access denied at the object store (SRS §C01, 403)."""

    code = "storage_forbidden"


class QuotaExceeded(StorageError):
    """Per-file or KB quota exceeded (SRS §C01 file_too_large / quota_exceeded, 413)."""

    code = "quota_exceeded"


# ---------------------------------------------------------------------------
# Existence (finer-grained than missing-object integrity incident)
# ---------------------------------------------------------------------------


class ObjectNotFound(StorageError):
    """No object exists for this storage_object_id (plain lookup miss)."""

    code = "object_not_found"


class ObjectAlreadyExists(StorageError):
    """Object already exists where a create-only put was requested."""

    code = "object_already_exists"


__all__ = [
    "ChecksumMismatch",
    "ObjectAlreadyExists",
    "ObjectNotFound",
    "QuotaExceeded",
    "StorageError",
    "StorageForbidden",
    "StorageObjectCorrupt",
    "StorageObjectMissing",
    "StorageUnavailable",
]
