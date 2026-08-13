"""Tests for the filesystem-backed ``FakeObjectStore`` (M1.1, WP1A).

Covers the ``ObjectStorePort`` contract over a real on-disk backend, including
cross-instance persistence (instance A writes, instance B reads via the same
root). No third-party dependencies.

References:
- SRS §C00, §3.1A, §3.1B, §9.5
- ADR-0003 D-002 (dual adapter), D-006 (Fake for tests), D-020 (location)
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from knowledge_mining.mining.contracts.storage import (
    ChecksumMismatch,
    ObjectLocation,
    ObjectStorePort,
    PartETag,
    PresignedAccess,
    PutOptions,
    PutResult,
    StorageObjectMissing,
    UploadTicket,
)
from knowledge_mining.mining.infra.object_store import FakeObjectStore
from knowledge_mining.mining.infra.object_store.keys import build_object_key

_PAYLOAD = b"the quick brown fox jumps over the lazy dog" * 256  # ~11 KiB


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    # Emit in 4 KiB chunks to exercise the streaming path.
    for i in range(0, len(data), 4096):
        yield data[i : i + 4096]


def _key_for(data: bytes, artifact_class: str = "source") -> str:
    return build_object_key(artifact_class, hashlib.sha256(data).hexdigest())


def _loc(data: bytes, bucket: str = "test-bucket", artifact_class: str = "source") -> ObjectLocation:
    return ObjectLocation(bucket=bucket, object_key=_key_for(data, artifact_class))


@pytest.fixture
def store(tmp_path: Path) -> FakeObjectStore:
    return FakeObjectStore(root_path=str(tmp_path / "store"))


def test_fake_satisfies_object_store_port(store: FakeObjectStore) -> None:
    assert isinstance(store, ObjectStorePort)


@pytest.mark.asyncio
async def test_put_get_roundtrip_large_streamed(store: FakeObjectStore) -> None:
    payload = _PAYLOAD * 64  # ~700 KiB, exercises chunked stream
    location = _loc(payload)
    result = await store.put_stream(location, _stream(payload), PutOptions(artifact_class="source"))

    assert isinstance(result, PutResult)
    assert result.size == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    # D-020: no storage_object_id on PutResult.
    assert not hasattr(result, "storage_object_id")

    collected = b"".join([c async for c in store.get_stream(location)])
    assert collected == payload


@pytest.mark.asyncio
async def test_stat_reports_metadata(store: FakeObjectStore) -> None:
    payload = b"stat payload"
    location = _loc(payload)
    await store.put_stream(location, _stream(payload), PutOptions(mime="text/plain"))

    stat = await store.stat(location)
    assert stat.size == len(payload)
    assert stat.sha256 == hashlib.sha256(payload).hexdigest()
    assert stat.mime == "text/plain"
    assert stat.bucket == location.bucket
    assert stat.object_key == location.object_key


@pytest.mark.asyncio
async def test_head_exists_and_delete(store: FakeObjectStore) -> None:
    location = _loc(b"x")
    await store.put_stream(location, _stream(b"x"), PutOptions())

    assert await store.head_exists(location) is True
    await store.delete(location)
    assert await store.head_exists(location) is False
    with pytest.raises(StorageObjectMissing):
        await store.stat(location)


@pytest.mark.asyncio
async def test_copy_produces_same_sha256(store: FakeObjectStore) -> None:
    payload = b"copy me" * 100
    src = _loc(payload)
    dst = _loc(payload, artifact_class="parse")
    # Different object_key via different artifact_class prefix path component,
    # but same content hash -> distinct keys.
    dst = ObjectLocation(bucket="test-bucket", object_key="dst/" + secrets.token_hex(8))

    src_res = await store.put_stream(src, _stream(payload), PutOptions(artifact_class="source"))
    dst_res = await store.copy(src, dst, PutOptions(artifact_class="backend_raw"))

    assert dst != src
    assert dst_res.sha256 == src_res.sha256
    got = b"".join([c async for c in store.get_stream(dst)])
    assert got == payload


@pytest.mark.asyncio
async def test_presign_get_returns_location_url(store: FakeObjectStore) -> None:
    location = _loc(b"x")
    await store.put_stream(location, _stream(b"x"), PutOptions())
    access = await store.presign_get(location, expires_in_seconds=120)

    assert isinstance(access, PresignedAccess)
    assert access.method == "GET"
    assert location.bucket in access.url
    assert location.object_key in access.url
    assert access.expires_in_seconds == 120
    assert access.location == location


@pytest.mark.asyncio
async def test_presign_put_for_unknown_object(store: FakeObjectStore) -> None:
    location = _loc(b"y")
    access = await store.presign_put(location)
    assert access.method == "PUT"
    assert access.location == location


@pytest.mark.asyncio
async def test_multipart_init_upload_complete_roundtrip(store: FakeObjectStore) -> None:
    big = bytes(range(256)) * 2048  # 512 KiB
    location = ObjectLocation(bucket="test-bucket", object_key="obj/multipart-1")
    ticket = await store.initiate_multipart(location, PutOptions(artifact_class="source"))

    assert isinstance(ticket, UploadTicket)
    assert ticket.upload_id
    assert ticket.location == location

    third = len(big) // 3
    parts: list[PartETag] = []
    for i, pnum in enumerate([1, 2, 3]):
        chunk = big[i * third : (i + 1) * third] if i < 2 else big[i * third :]
        etag = await store.upload_part(ticket.upload_id, pnum, _stream(chunk))
        assert etag.part_number == pnum
        parts.append(etag)

    result = await store.complete_multipart(ticket.upload_id, tuple(parts))
    assert result.sha256 == hashlib.sha256(big).hexdigest()
    assert result.size == len(big)

    got = b"".join([c async for c in store.get_stream(location)])
    assert got == big


@pytest.mark.asyncio
async def test_multipart_complete_with_expected_sha_mismatch(store: FakeObjectStore) -> None:
    payload = b"multipart mismatch payload"
    location = ObjectLocation(bucket="test-bucket", object_key="obj/multipart-bad")
    ticket = await store.initiate_multipart(location, PutOptions())
    await store.upload_part(ticket.upload_id, 1, _stream(payload))

    with pytest.raises(ChecksumMismatch):
        await store.complete_multipart(
            ticket.upload_id,
            (PartETag(part_number=1, etag="deadbeef"),),
            expected_sha256="0" * 64,
        )
    # Object must NOT exist after a failed complete.
    assert await store.head_exists(location) is False


@pytest.mark.asyncio
async def test_abort_multipart_discards_session(store: FakeObjectStore) -> None:
    location = ObjectLocation(bucket="test-bucket", object_key="obj/mp-abort")
    ticket = await store.initiate_multipart(location, PutOptions())
    await store.upload_part(ticket.upload_id, 1, _stream(b"chunk"))
    await store.abort_multipart(ticket.upload_id)

    root = Path(store._root)  # type: ignore[attr-defined]
    upload_dir = root / "_multipart" / ticket.upload_id
    assert not upload_dir.exists()


@pytest.mark.asyncio
async def test_expected_sha256_mismatch_on_put(store: FakeObjectStore) -> None:
    location = _loc(b"real")
    with pytest.raises(ChecksumMismatch) as exc_info:
        await store.put_stream(
            location,
            _stream(b"real bytes"),
            PutOptions(expected_sha256="0" * 64),
        )
    assert exc_info.value.expected == "0" * 64
    assert exc_info.value.actual == hashlib.sha256(b"real bytes").hexdigest()
    # Failed put must leave no object behind.
    assert await store.head_exists(location) is False


@pytest.mark.asyncio
async def test_get_missing_raises(store: FakeObjectStore) -> None:
    location = ObjectLocation(bucket="test-bucket", object_key="obj/missing")

    async def _collect() -> bytes:
        return b"".join([c async for c in store.get_stream(location)])

    with pytest.raises(StorageObjectMissing):
        await _collect()


@pytest.mark.asyncio
async def test_stat_missing_raises(store: FakeObjectStore) -> None:
    location = ObjectLocation(bucket="test-bucket", object_key="obj/missing2")
    with pytest.raises(StorageObjectMissing):
        await store.stat(location)


@pytest.mark.asyncio
async def test_persistence_across_instances(tmp_path: Path) -> None:
    """Instance A writes; a fresh instance B with the same root reads it back."""
    root = str(tmp_path / "shared")
    payload = b"persistent bytes" * 100
    location = _loc(payload)

    store_a = FakeObjectStore(root_path=root)
    await store_a.put_stream(location, _stream(payload), PutOptions())

    store_b = FakeObjectStore(root_path=root)
    assert await store_b.head_exists(location) is True
    got = b"".join([c async for c in store_b.get_stream(location)])
    assert got == payload
    stat_b = await store_b.stat(location)
    assert stat_b.sha256 == hashlib.sha256(payload).hexdigest()
