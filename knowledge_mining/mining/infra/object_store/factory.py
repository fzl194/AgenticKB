"""Adapter factory for the Object Store (M1.1, WP1A).

``make_object_store(config)`` selects the ``ObjectStorePort`` implementation by
``provider``:

- ``"fake"``  -> ``FakeObjectStore`` (filesystem; tests + local dev)
- ``"minio"`` -> ``MinioObjectStore`` (production; lazy SDK import)

References:
- SRS §C00 (MinIO Object Storage Foundation)
- ADR-0003 D-002 (dual adapter), D-006 (guarded MinIO)
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore


def make_object_store(config: ObjectStoreConfig) -> ObjectStorePort:
    """Return the ``ObjectStorePort`` for ``config.provider``.

    Lazy on ``minio``: constructing a MinIO adapter imports the SDK; the Fake
    adapter has no third-party dependency.
    """
    if config.provider == "fake":
        return FakeObjectStore(root_path=config.root_path)
    if config.provider == "minio":
        from knowledge_mining.mining.infra.object_store.minio import MinioObjectStore

        return MinioObjectStore(config)
    raise ValueError(f"unknown object store provider: {config.provider!r}")


__all__ = ["make_object_store"]
