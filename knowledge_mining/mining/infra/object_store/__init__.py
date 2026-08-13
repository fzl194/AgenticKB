"""Object Store adapters (M1.1, WP1A).

Two ``ObjectStorePort`` implementations live here:

- ``FakeObjectStore`` — filesystem-backed, cross-instance persistent. Used by
  all unit tests and local dev (ADR-0003 D-002, D-006).
- ``MinioObjectStore`` — production adapter over the MinIO SDK. The SDK is
  imported lazily inside ``__init__`` and methods, so this module imports
  cleanly even when ``minio`` is not installed (ADR-0003 D-006).

``make_object_store(config)`` selects the adapter by ``provider``.

References:
- SRS §C00 (MinIO Object Storage Foundation)
- SRS §8.1 (bucket / object_key strategy), §8.7, §8.9
- ADR-0003 D-002 (dual adapter), D-006 (guarded MinIO), D-020 (location)
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.storage.types import ObjectLocation
from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig
from knowledge_mining.mining.infra.object_store.factory import make_object_store
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.infra.object_store.keys import build_object_key
from knowledge_mining.mining.infra.object_store.minio import MinioObjectStore

__all__ = [
    "FakeObjectStore",
    "MinioObjectStore",
    "ObjectLocation",
    "ObjectStoreConfig",
    "build_object_key",
    "make_object_store",
]
