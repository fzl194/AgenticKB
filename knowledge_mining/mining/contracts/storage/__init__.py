"""Layer 1: Object Store contract — Port, types, errors, enums (WP0.3).

Defines the seam between business/parse layers and the object store. Pure
stdlib: ``@dataclass(frozen=True)`` + ``@runtime_checkable Protocol``, no
Pydantic, no MinIO/boto3 imports, no business/DB/FastAPI dependencies
(ADR-0003 D-001). The production ``MinioObjectStore`` adapter (M1, WP1A)
implements ``ObjectStorePort``; tests use an in-memory ``FakeObjectStore``
(ADR-0003 D-002, D-006).

References:
- SRS §C00 (MinIO Object Storage Foundation)
- SRS §3.1A (Storage Object), §3.1B (Upload Session), §9.0A/B (state machines)
- ADR-0003 D-002 (dual adapter), D-006 (Fake for tests), D-007 (tradeoffs),
  D-020 (Port changed to ObjectLocation addressing, M1)
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.storage.enums import (
    VALID_ARTIFACT_CLASSES,
    VALID_PRESIGN_METHODS,
    VALID_PROVIDERS,
    VALID_STORAGE_OBJECT_STATES,
    VALID_UPLOAD_SESSION_STATES,
)
from knowledge_mining.mining.contracts.storage.errors import (
    ChecksumMismatch,
    ObjectAlreadyExists,
    ObjectNotFound,
    QuotaExceeded,
    StorageError,
    StorageForbidden,
    StorageObjectCorrupt,
    StorageObjectMissing,
    StorageUnavailable,
)
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import (
    ObjectLocation,
    ObjectRef,
    ObjectStat,
    PartETag,
    PresignedAccess,
    PutOptions,
    PutResult,
    UploadTicket,
)

__all__ = [
    # Port
    "ObjectStorePort",
    # Types
    "ObjectLocation",
    "ObjectRef",
    "ObjectStat",
    "PartETag",
    "PresignedAccess",
    "PutOptions",
    "PutResult",
    "UploadTicket",
    # Enums
    "VALID_ARTIFACT_CLASSES",
    "VALID_PRESIGN_METHODS",
    "VALID_PROVIDERS",
    "VALID_STORAGE_OBJECT_STATES",
    "VALID_UPLOAD_SESSION_STATES",
    # Errors
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
