"""Enum-adjacent constants for the Object Store contract (WP0.3).

Mirrors DB CHECK constraints and SRS state machines. Pure stdlib frozensets,
following the Layer-1 convention in ``contracts/models.py``.

References:
- SRS §3.1A (artifact_class), §3.1B + §9.0A (upload session states)
- SRS §9.0B (storage object states)
- ADR-0003 D-001 (frozen dataclass + VALID_* frozenset style)
"""
from __future__ import annotations

# ``VALID_ARTIFACT_CLASSES`` is the shared source of truth for the artifact
# classification across the Parse IR (WP0.2) and Storage (WP0.3) contracts.
# Re-exported here so the storage package is self-documenting without creating
# a second definition that could drift.
from knowledge_mining.mining.contracts.parse_ir.enums import VALID_ARTIFACT_CLASSES

# --- Storage object states (SRS §3.1A, §9.0B) --------------------------------
# STAGING -> AVAILABLE -> DELETING -> DELETED
# STAGING / AVAILABLE -> QUARANTINED
# AVAILABLE -> MISSING / CORRUPT  (integrity incidents, not business deletes)
VALID_STORAGE_OBJECT_STATES: frozenset[str] = frozenset({
    "STAGING",
    "AVAILABLE",
    "QUARANTINED",
    "DELETING",
    "DELETED",
    "MISSING",
    "CORRUPT",
})

# --- Upload session states (SRS §3.1B, §9.0A) --------------------------------
# INITIATED -> UPLOADING -> OBJECT_STAGED -> VERIFYING -> COMMITTED
#           -> ABORTED / EXPIRED
# OBJECT_STAGED / VERIFYING -> REJECTED
VALID_UPLOAD_SESSION_STATES: frozenset[str] = frozenset({
    "INITIATED",
    "UPLOADING",
    "OBJECT_STAGED",
    "VERIFYING",
    "COMMITTED",
    "ABORTED",
    "EXPIRED",
    "REJECTED",
})

# --- Presigned URL verbs (SRS §C00) ------------------------------------------
VALID_PRESIGN_METHODS: frozenset[str] = frozenset({"GET", "PUT"})

# --- Storage providers (ADR-0003 D-002: dual adapter) ------------------------
# ``minio`` is the production adapter (WP1A); ``fake`` is the in-memory /
# filesystem simulation used by all tests and local dev.
VALID_PROVIDERS: frozenset[str] = frozenset({"minio", "fake"})


__all__ = [
    "VALID_ARTIFACT_CLASSES",
    "VALID_PRESIGN_METHODS",
    "VALID_PROVIDERS",
    "VALID_STORAGE_OBJECT_STATES",
    "VALID_UPLOAD_SESSION_STATES",
]
