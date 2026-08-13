"""Pytest suite for the Object Store Port contract (WP0.3, revised D-020 M1).

References:
- SRS §C00 (MinIO Object Storage Foundation)
- SRS §C01 (error code -> HTTP table)
- SRS §3.1A (Storage Object), §3.1B (Upload Session)
- SRS §9.0A / §9.0B (state machines), §9.5 (recovery)
- ADR-0003 D-002 (dual adapter: Fake + MinIO), D-006 (Fake for tests),
  D-007 (signature tradeoffs for this Port), D-020 (ObjectLocation addressing)

The in-memory ``FakeObjectStore`` defined here exists ONLY to prove the
``ObjectStorePort`` Protocol is implementable and usable with location-based
addressing. It is NOT a production adapter — the real ``MinioObjectStore``
(WP1A) lands in M1 and is excluded from these tests per D-006; a separate
filesystem-backed ``FakeObjectStore`` lives in ``mining/infra/object_store/``
and is covered by infra tests.
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator

import pytest

from knowledge_mining.mining.contracts.storage import (
    ChecksumMismatch,
    ObjectLocation,
    ObjectStat,
    ObjectStorePort,
    PartETag,
    PresignedAccess,
    PutOptions,
    PutResult,
    StorageObjectMissing,
    UploadTicket,
)
from knowledge_mining.mining.contracts.storage.enums import (
    VALID_ARTIFACT_CLASSES,
    VALID_PROVIDERS,
    VALID_STORAGE_OBJECT_STATES,
    VALID_UPLOAD_SESSION_STATES,
)


# ---------------------------------------------------------------------------
# In-memory FakeObjectStore — contract verification only, NOT production.
# ---------------------------------------------------------------------------


class FakeObjectStore:
    """Minimal in-memory implementation of ``ObjectStorePort`` (D-020).

    State is keyed by ``ObjectLocation``. Multipart sessions are held in a
    separate dict until completed. SHA-256 is computed over the reassembled
    byte stream, matching how the Port contract computes checksums server-side
    (SRS §3.1A).
    """

    provider = "fake"

    def __init__(self, bucket: str = "test-bucket") -> None:
        self._bucket = bucket
        # (bucket, object_key) -> dict(data, sha256, size, mime, artifact_class, etag)
        self._objects: dict[tuple[str, str], dict] = {}
        # upload_id -> multipart session
        self._uploads: dict[str, dict] = {}

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def _astream(self, data: bytes) -> AsyncIterator[bytes]:
        # Yield in one chunk; the contract treats this as an opaque stream.
        yield data

    # -- ObjectStorePort ---------------------------------------------------

    async def put_stream(
        self,
        location: ObjectLocation,
        stream: AsyncIterator[bytes],
        options: PutOptions,
    ) -> PutResult:
        if options.artifact_class not in VALID_ARTIFACT_CLASSES:
            raise ValueError(f"unknown artifact_class: {options.artifact_class}")
        data = await self._drain(stream)
        sha = self._sha256(data)
        if options.expected_sha256 is not None and options.expected_sha256 != sha:
            raise ChecksumMismatch(
                f"expected {options.expected_sha256}, got {sha}",
                expected=options.expected_sha256,
                actual=sha,
            )
        self._objects[(location.bucket, location.object_key)] = {
            "data": data,
            "sha256": sha,
            "size": len(data),
            "mime": options.mime,
            "artifact_class": options.artifact_class,
            "etag": sha[:32],
        }
        return PutResult(
            version_id=None,
            etag=sha[:32],
            sha256=sha,
            size=len(data),
        )

    async def get_stream(self, location: ObjectLocation) -> AsyncIterator[bytes]:
        obj = self._require(location)
        # async generator — return the bytes as a stream
        yield obj["data"]

    async def stat(self, location: ObjectLocation) -> ObjectStat:
        obj = self._require(location)
        return ObjectStat(
            bucket=location.bucket,
            object_key=location.object_key,
            size=obj["size"],
            sha256=obj["sha256"],
            etag=obj["etag"],
            mime=obj["mime"],
            artifact_class=obj["artifact_class"],
            encryption=None,
            version_id=None,
            last_verified_at=None,
        )

    async def delete(self, location: ObjectLocation) -> None:
        self._objects.pop((location.bucket, location.object_key), None)

    async def head_exists(self, location: ObjectLocation) -> bool:
        return (location.bucket, location.object_key) in self._objects

    async def copy(
        self,
        src: ObjectLocation,
        dst: ObjectLocation,
        options: PutOptions,
    ) -> PutResult:
        src_obj = self._require(src)
        # Server-side copy: same bytes => same sha256.
        return await self.put_stream(dst, self._astream(src_obj["data"]), options)

    async def presign_get(
        self,
        location: ObjectLocation,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        self._require(location)
        return PresignedAccess(
            method="GET",
            url=(
                f"fake://get/{location.bucket}/{location.object_key}"
                f"?expires={expires_in_seconds}"
            ),
            expires_in_seconds=expires_in_seconds,
            location=location,
        )

    async def presign_put(
        self,
        location: ObjectLocation,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        return PresignedAccess(
            method="PUT",
            url=(
                f"fake://put/{location.bucket}/{location.object_key}"
                f"?expires={expires_in_seconds}"
            ),
            expires_in_seconds=expires_in_seconds,
            location=location,
        )

    async def initiate_multipart(
        self,
        location: ObjectLocation,
        options: PutOptions,
    ) -> UploadTicket:
        upload_id = "up_" + secrets.token_hex(12)
        self._uploads[upload_id] = {
            "location": location,
            "options": options,
            "parts": {},  # part_number -> bytes
        }
        return UploadTicket(
            upload_id=upload_id,
            location=location,
            parts_expected=None,
            presigned_part_urls=(),
        )

    async def upload_part(
        self,
        upload_id: str,
        part_number: int,
        stream: AsyncIterator[bytes],
    ) -> PartETag:
        session = self._uploads[upload_id]
        data = await self._drain(stream)
        session["parts"][part_number] = data
        return PartETag(part_number=part_number, etag=self._sha256(data)[:32])

    async def complete_multipart(
        self,
        upload_id: str,
        parts: tuple[PartETag, ...],
        expected_sha256: str | None = None,
    ) -> PutResult:
        session = self._uploads.pop(upload_id)
        ordered = b"".join(
            session["parts"][p.part_number]
            for p in sorted(parts, key=lambda x: x.part_number)
        )
        options: PutOptions = session["options"]
        location: ObjectLocation = session["location"]
        # Reuse put_stream to materialize the assembled object.
        result = await self.put_stream(location, self._astream(ordered), options)
        if expected_sha256 is not None and expected_sha256 != result.sha256:
            raise ChecksumMismatch(
                f"multipart expected {expected_sha256}, got {result.sha256}",
                expected=expected_sha256,
                actual=result.sha256,
            )
        return result

    async def abort_multipart(self, upload_id: str) -> None:
        self._uploads.pop(upload_id, None)

    # -- internals ---------------------------------------------------------

    async def _drain(self, stream: AsyncIterator[bytes]) -> bytes:
        chunks: list[bytes] = []
        async for chunk in stream:
            chunks.append(chunk)
        return b"".join(chunks)

    def _require(self, location: ObjectLocation) -> dict:
        obj = self._objects.get((location.bucket, location.object_key))
        if obj is None:
            raise StorageObjectMissing(f"{location.bucket}/{location.object_key}")
        return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> FakeObjectStore:
    return FakeObjectStore()


def _loc(key: str = "obj/" + secrets.token_hex(8), bucket: str = "test-bucket") -> ObjectLocation:
    return ObjectLocation(bucket=bucket, object_key=key)


async def _bytes(data: bytes) -> AsyncIterator[bytes]:
    yield data


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_fake_satisfies_object_store_port(store: FakeObjectStore) -> None:
    """The Fake must be recognized as an ObjectStorePort (structural typing)."""
    assert isinstance(store, ObjectStorePort)


def test_enum_constants_are_frozensets() -> None:
    for fs in (
        VALID_ARTIFACT_CLASSES,
        VALID_PROVIDERS,
        VALID_STORAGE_OBJECT_STATES,
        VALID_UPLOAD_SESSION_STATES,
    ):
        assert isinstance(fs, frozenset)
    assert "source" in VALID_ARTIFACT_CLASSES
    assert "minio" in VALID_PROVIDERS and "fake" in VALID_PROVIDERS
    assert "AVAILABLE" in VALID_STORAGE_OBJECT_STATES
    assert "CORRUPT" in VALID_STORAGE_OBJECT_STATES  # SRS §9.0B
    assert "COMMITTED" in VALID_UPLOAD_SESSION_STATES


def test_object_location_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    loc = ObjectLocation(bucket="b", object_key="k", version_id=None)
    with pytest.raises(FrozenInstanceError):
        loc.bucket = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Round-trip: put -> get -> stat -> head_exists -> delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_get_roundtrip(store: FakeObjectStore) -> None:
    payload = b"hello object store" * 64
    result = await store.put_stream(_loc(), _bytes(payload), PutOptions(artifact_class="source"))

    assert isinstance(result, PutResult)
    assert result.size == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    # D-020: PutResult no longer carries storage_object_id.
    assert not hasattr(result, "storage_object_id")


@pytest.mark.asyncio
async def test_stat_reports_sha256_and_size(store: FakeObjectStore) -> None:
    payload = b"stat me"
    location = _loc()
    await store.put_stream(location, _bytes(payload), PutOptions(mime="text/plain"))

    stat = await store.stat(location)
    assert isinstance(stat, ObjectStat)
    assert stat.sha256 == hashlib.sha256(payload).hexdigest()
    assert stat.size == len(payload)
    assert stat.mime == "text/plain"
    assert stat.artifact_class == "source"
    assert stat.bucket == location.bucket
    assert stat.object_key == location.object_key


@pytest.mark.asyncio
async def test_head_exists_and_delete(store: FakeObjectStore) -> None:
    location = _loc()
    await store.put_stream(location, _bytes(b"x"), PutOptions())

    assert await store.head_exists(location) is True
    await store.delete(location)
    assert await store.head_exists(location) is False
    with pytest.raises(StorageObjectMissing):
        await store.stat(location)


# ---------------------------------------------------------------------------
# Presign
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_presign_get_returns_url(store: FakeObjectStore) -> None:
    location = _loc()
    await store.put_stream(location, _bytes(b"x"), PutOptions())
    access = await store.presign_get(location, expires_in_seconds=300)

    assert isinstance(access, PresignedAccess)
    assert access.method == "GET"
    assert access.url.startswith("fake://get/")
    assert access.expires_in_seconds == 300
    assert access.location == location


@pytest.mark.asyncio
async def test_presign_put_for_unknown_object(store: FakeObjectStore) -> None:
    location = _loc(bucket="bucket-a", key="obj/key-1")
    access = await store.presign_put(location)
    assert access.method == "PUT"
    assert "bucket-a" in access.url and "obj/key-1" in access.url
    assert access.location == location


# ---------------------------------------------------------------------------
# Multipart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multipart_roundtrip(store: FakeObjectStore) -> None:
    big = bytes(range(256)) * 1024  # 256 KiB
    location = _loc(key="obj/multipart-1")
    ticket = await store.initiate_multipart(location, PutOptions(artifact_class="source"))
    assert isinstance(ticket, UploadTicket)
    assert ticket.upload_id
    assert ticket.location == location

    # Split into 3 parts.
    third = len(big) // 3
    parts: list[PartETag] = []
    for i, pnum in enumerate([1, 2, 3]):
        chunk = big[i * third : (i + 1) * third] if i < 2 else big[i * third :]
        etag = await store.upload_part(ticket.upload_id, pnum, _bytes(chunk))
        assert etag.part_number == pnum
        parts.append(etag)

    result = await store.complete_multipart(ticket.upload_id, tuple(parts))

    got = b"".join([c async for c in store.get_stream(location)])
    assert got == big
    assert result.sha256 == hashlib.sha256(big).hexdigest()


@pytest.mark.asyncio
async def test_abort_multipart_discards_session(store: FakeObjectStore) -> None:
    location = _loc(key="k")
    ticket = await store.initiate_multipart(location, PutOptions())
    await store.upload_part(ticket.upload_id, 1, _bytes(b"chunk"))
    await store.abort_multipart(ticket.upload_id)
    # Aborted upload_id is no longer completable.
    with pytest.raises(KeyError):
        await store.complete_multipart(ticket.upload_id, ())


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_produces_same_sha256(store: FakeObjectStore) -> None:
    payload = b"copy me" * 100
    src_loc = _loc()
    dst_loc = _loc(key="obj/dst")
    src = await store.put_stream(src_loc, _bytes(payload), PutOptions(artifact_class="source"))
    dst = await store.copy(src_loc, dst_loc, PutOptions(artifact_class="backend_raw"))

    assert dst_loc != src_loc
    assert dst.sha256 == src.sha256  # same bytes => same hash


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checksum_mismatch_on_expected_sha256(store: FakeObjectStore) -> None:
    with pytest.raises(ChecksumMismatch) as exc_info:
        await store.put_stream(
            _loc(),
            _bytes(b"real bytes"),
            PutOptions(expected_sha256="0" * 64),
        )
    err = exc_info.value
    assert err.code == "checksum_mismatch"
    assert err.expected == "0" * 64
    assert err.actual == hashlib.sha256(b"real bytes").hexdigest()


@pytest.mark.asyncio
async def test_get_stream_missing_raises(store: FakeObjectStore) -> None:
    with pytest.raises(StorageObjectMissing):
        await store.stat(_loc(key="obj/does_not_exist"))


def test_error_subclasses_carry_stable_codes() -> None:
    from knowledge_mining.mining.contracts.storage.errors import (
        QuotaExceeded,
        StorageForbidden,
        StorageObjectCorrupt,
        StorageUnavailable,
    )

    assert StorageUnavailable("x").code == "storage_unavailable"
    assert StorageObjectMissing("x").code == "storage_object_missing"
    assert StorageObjectCorrupt("x").code == "storage_object_corrupt"
    assert ChecksumMismatch("x").code == "checksum_mismatch"
    assert StorageForbidden("x").code == "storage_forbidden"
    assert QuotaExceeded("x").code == "quota_exceeded"


def test_dataclasses_are_frozen() -> None:
    from dataclasses import FrozenInstanceError

    result = PutResult(
        version_id=None,
        etag="e",
        sha256="a" * 64,
        size=10,
    )
    with pytest.raises(FrozenInstanceError):
        result.size = 99  # type: ignore[misc]
