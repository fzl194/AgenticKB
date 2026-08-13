"""Frozen dataclasses for the Object Store contract (WP0.3).

All types are pure-stdlib frozen dataclasses, consistent with
``contracts/models.py``. They carry NO long-term credentials and NO MinIO SDK
types (SRS §C00): ``ObjectRef`` / ``ObjectStat`` expose only the business
identity and observability metadata an upstream layer needs.

References:
- SRS §3.1A (Storage Object minimum info)
- SRS §3.1B (Upload Session)
- SRS §C00 (public port exposes only project types)
- ADR-0003 D-001 (frozen dataclass + Protocol, no Pydantic)
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Object identity (SRS §3.1A)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObjectRef:
    """Business-facing pointer to a single immutable object.

    Carries only what an upstream layer needs to *name* the object — never the
    long-term credentials needed to *read* it. ``object_key`` is the
    system-generated immutable key with no business semantics (SRS §3.1A);
    ``object_version_id`` is MinIO's operational versioning and is NOT exposed
    to users as a document version.
    """

    storage_object_id: str
    provider: str  # minio | fake  (see enums.VALID_PROVIDERS)
    bucket: str
    object_key: str
    object_version_id: str | None = None


@dataclass(frozen=True)
class ObjectStat:
    """Stat response — object metadata, no bytes (SRS §3.1A)."""

    storage_object_id: str
    size: int
    sha256: str | None
    etag: str | None = None
    mime: str | None = None
    artifact_class: str = "source"  # see enums.VALID_ARTIFACT_CLASSES
    encryption: str | None = None
    version_id: str | None = None
    last_verified_at: str | None = None


# ---------------------------------------------------------------------------
# Put path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PutOptions:
    """Options for ``put_stream`` / ``copy`` / ``initiate_multipart``.

    ``expected_sha256`` makes the put fail closed when the caller already knows
    the content hash (e.g. upload-session verify step, SRS §C01 checksum_mismatch).
    """

    artifact_class: str = "source"
    mime: str | None = None
    expected_sha256: str | None = None
    content_length: int | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PutResult:
    """Result of a successful put/copy/multipart-complete.

    ``sha256`` is the authoritative content checksum computed server-side
    (SRS §3.1A). ``storage_object_id`` is the new business identity assigned
    by the adapter.
    """

    storage_object_id: str
    version_id: str | None
    etag: str | None
    sha256: str
    size: int


# ---------------------------------------------------------------------------
# Multipart (SRS §3.1B, §C00)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UploadTicket:
    """Handle for a resumable multipart upload.

    ``storage_object_id`` is empty until the upload completes and a Storage
    Object is materialized. ``presigned_part_urls`` may be precomputed so
    clients upload parts directly to the object store.
    """

    upload_id: str
    storage_object_id: str
    bucket: str
    object_key: str
    parts_expected: int | None = None
    presigned_part_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class PartETag:
    """ETag returned for one uploaded part."""

    part_number: int
    etag: str


# ---------------------------------------------------------------------------
# Presigned access (SRS §C00)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PresignedAccess:
    """A short-lived signed URL granting GET or PUT on a single object.

    For ``presign_put`` the object does not exist yet, so
    ``storage_object_id`` is empty; for ``presign_get`` it identifies the
    existing object.
    """

    method: str  # GET | PUT  (see enums.VALID_PRESIGN_METHODS)
    url: str
    expires_in_seconds: int
    storage_object_id: str
    object_key: str


__all__ = [
    "ObjectRef",
    "ObjectStat",
    "PartETag",
    "PresignedAccess",
    "PutOptions",
    "PutResult",
    "UploadTicket",
]
