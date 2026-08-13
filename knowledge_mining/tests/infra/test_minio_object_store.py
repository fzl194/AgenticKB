"""Tests for the MinIO adapter pure helpers + guarded smoke (M1.1, WP1A).

These tests do NOT import the ``minio`` SDK. They exercise the pure mapping /
bucket-naming helpers directly. A real round-trip smoke test is gated behind
``RUN_MINIO_SMOKE`` env var (ADR-0003 D-006) and is skipped by default.

References:
- SRS §C00, §8.1, §9.5
- ADR-0003 D-002 (dual adapter), D-006 (guarded smoke), D-020 (location)
"""
from __future__ import annotations

import os

import pytest

from knowledge_mining.mining.contracts.storage.errors import (
    StorageForbidden,
    StorageObjectMissing,
    StorageUnavailable,
)
from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig
from knowledge_mining.mining.infra.object_store.minio import (
    _bucket_for,
    _extract_code,
    _map_s3_error,
)


class _FakeS3Error(Exception):
    """Stand-in for ``minio.error.S3Error`` (we must not import minio here)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"S3Error code={code}")


# ---------------------------------------------------------------------------
# _bucket_for (SRS §8.1)
# ---------------------------------------------------------------------------


def test_bucket_for_concatenates_prefix_and_artifact_class() -> None:
    cfg = ObjectStoreConfig(provider="minio", bucket_prefix="agentickb-dev-")
    assert _bucket_for(cfg, "source") == "agentickb-dev-source"
    assert _bucket_for(cfg, "parse") == "agentickb-dev-parse"
    assert _bucket_for(cfg, "binary") == "agentickb-dev-binary"
    assert _bucket_for(cfg, "staging") == "agentickb-dev-staging"


# ---------------------------------------------------------------------------
# _extract_code / _map_s3_error (SRS §9.5 — never mask as 404)
# ---------------------------------------------------------------------------


def test_extract_code_reads_code_attr() -> None:
    assert _extract_code(_FakeS3Error("NoSuchKey")) == "NoSuchKey"
    assert _extract_code(Exception("no code")) is None


def test_map_s3_error_no_such_key_becomes_missing() -> None:
    err = _map_s3_error(_FakeS3Error("NoSuchKey"))
    assert isinstance(err, StorageObjectMissing)


def test_map_s3_error_no_such_bucket_becomes_missing() -> None:
    err = _map_s3_error(_FakeS3Error("NoSuchBucket"))
    assert isinstance(err, StorageObjectMissing)


def test_map_s3_error_access_denied_becomes_forbidden() -> None:
    err = _map_s3_error(_FakeS3Error("AccessDenied"))
    assert isinstance(err, StorageForbidden)


def test_map_s3_error_forbidden_becomes_forbidden() -> None:
    err = _map_s3_error(_FakeS3Error("Forbidden"))
    assert isinstance(err, StorageForbidden)


def test_map_s3_error_unknown_becomes_unavailable_not_missing() -> None:
    # SRS §9.5: unknown / transient MUST NOT be masked as object-not-found.
    err = _map_s3_error(_FakeS3Error("InternalError"))
    assert isinstance(err, StorageUnavailable)
    assert not isinstance(err, StorageObjectMissing)


def test_map_s3_error_plain_exception_becomes_unavailable() -> None:
    err = _map_s3_error(RuntimeError("network blip"))
    assert isinstance(err, StorageUnavailable)


# ---------------------------------------------------------------------------
# Guarded smoke (D-006) — only runs when RUN_MINIO_SMOKE is set
# ---------------------------------------------------------------------------


_MINIO_SMOKE = bool(os.environ.get("RUN_MINIO_SMOKE"))


@pytest.mark.skipif(not _MINIO_SMOKE, reason="RUN_MINIO_SMOKE not set (D-006 guard)")
@pytest.mark.asyncio
async def test_minio_smoke_put_get_delete() -> None:
    """End-to-end smoke against a real MinIO. Requires the SDK installed.

    Configure via env vars: MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
    MINIO_BUCKET_PREFIX (optional). Skipped unless RUN_MINIO_SMOKE is set.
    """
    import hashlib
    import secrets

    from knowledge_mining.mining.contracts.storage import ObjectLocation, PutOptions
    from knowledge_mining.mining.infra.object_store.factory import make_object_store

    cfg = ObjectStoreConfig(
        provider="minio",
        bucket_prefix=os.environ.get("MINIO_BUCKET_PREFIX", "agentickb-smoke-"),
        endpoint=os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=os.environ.get("MINIO_SECURE", "0") == "1",
    )
    store = make_object_store(cfg)
    await store.ensure_buckets(("source",))

    payload = b"minio smoke payload " + secrets.token_bytes(64)
    location = ObjectLocation(
        bucket=cfg.bucket_prefix + "source",
        object_key="smoke/" + secrets.token_hex(8),
    )
    try:
        result = await store.put_stream(location, _one(payload), PutOptions(mime="text/plain"))
        assert result.sha256 == hashlib.sha256(payload).hexdigest()
        got = b"".join([c async for c in store.get_stream(location)])
        assert got == payload
        assert await store.head_exists(location) is True
    finally:
        await store.delete(location)


async def _one(data: bytes):
    yield data
