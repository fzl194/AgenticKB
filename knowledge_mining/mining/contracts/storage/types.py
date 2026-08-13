"""Frozen dataclasses for the Object Store contract (WP0.3, revised D-020 M1).

All types are pure-stdlib frozen dataclasses, consistent with
``contracts/models.py``. They carry NO long-term credentials and NO MinIO SDK
types (SRS §C00): ``ObjectLocation`` / ``ObjectStat`` expose only the addressing
and observability metadata an upstream layer needs.

Identity model (ADR-0003 D-020, supersedes D-013 #1):
  - The Port addresses object bytes by ``ObjectLocation(bucket, object_key,
    version_id?)`` — the native S3/MinIO addressing model. The project business
    identity ``storage_object_id`` is owned by the Repository (M1.2 / WP1B) and
    backed by the PG ``asset_storage_objects`` registry; the Port no longer
    knows it.
  - ``ObjectRef`` is retained for business-layer use (carries the business id)
    but the Port does not use it for addressing.

References:
- SRS §3.1A (Storage Object minimum info, key strategy)
- SRS §3.1B (Upload Session)
- SRS §C00 (public port exposes only project types)
- ADR-0003 D-001 (frozen dataclass + Protocol, no Pydantic)
- ADR-0003 D-020 (Port changed to ObjectLocation addressing, M1)
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Object location & identity (SRS §3.1A, D-020)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObjectLocation:
    """Physical address of an object's bytes in the store (SRS §3.1A, D-020).

    This is the native S3/MinIO addressing triple. It is the ONLY key the
    ``ObjectStorePort`` byte operations accept. The business identity
    ``storage_object_id`` lives one layer up (Repository -> PG registry) and is
    NOT part of location.

    ``version_id`` is the store's operational versioning (S3 object version),
    NOT a document business revision. ``None`` means the current version.
    """

    bucket: str
    object_key: str
    version_id: str | None = None


@dataclass(frozen=True)
class ObjectRef:
    """Business-facing pointer to a single immutable object.

    Carries the project business identity (``storage_object_id``) plus the
    physical ``ObjectLocation`` so upstream layers can name the object without
    re-resolving through the Repository. Used by the business/parse layer; the
    ``ObjectStorePort`` itself does NOT take this type — it takes
    ``ObjectLocation`` directly (D-020).
    """

    storage_object_id: str
    provider: str  # minio | fake  (see enums.VALID_PROVIDERS)
    bucket: str
    object_key: str
    object_version_id: str | None = None


@dataclass(frozen=True)
class ObjectStat:
    """Stat response — object metadata, no bytes (SRS §3.1A).

    After D-020 the Port no longer carries ``storage_object_id``; callers that
    need the business id already know it (they supplied the location). Stat is
    keyed by the location that was probed.
    """

    bucket: str
    object_key: str
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
    """Result of a successful put/copy/multipart-complete (D-020).

    ``sha256`` is the authoritative content checksum computed server-side
    (SRS §3.1A). The business identity ``storage_object_id`` is intentionally
    absent — it is assigned by the Repository (M1.2), not the store adapter.
    """

    version_id: str | None
    etag: str | None
    sha256: str
    size: int


# ---------------------------------------------------------------------------
# Multipart (SRS §3.1B, §C00)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UploadTicket:
    """Handle for a resumable multipart upload (D-020).

    ``location`` is the target ``ObjectLocation`` the caller chose at
    ``initiate_multipart`` time; the object is not yet materialized there until
    ``complete_multipart`` succeeds. ``presigned_part_urls`` may be precomputed
    so clients upload parts directly to the object store.
    """

    upload_id: str
    location: ObjectLocation
    parts_expected: int | None = None
    presigned_part_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class PartETag:
    """ETag returned for one uploaded part."""

    part_number: int
    etag: str


# ---------------------------------------------------------------------------
# Presigned access (SRS §C00, D-020)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PresignedAccess:
    """A short-lived signed URL granting GET or PUT on a single object (D-020).

    ``location`` is the object the URL targets — for ``presign_put`` the object
    does not exist yet, but the caller has still chosen its
    ``(bucket, object_key)``.
    """

    method: str  # GET | PUT  (see enums.VALID_PRESIGN_METHODS)
    url: str
    expires_in_seconds: int
    location: ObjectLocation


__all__ = [
    "ObjectLocation",
    "ObjectRef",
    "ObjectStat",
    "PartETag",
    "PresignedAccess",
    "PutOptions",
    "PutResult",
    "UploadTicket",
]
