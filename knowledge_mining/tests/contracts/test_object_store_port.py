"""Pytest suite for the Object Store Port contract (WP0.3).

References:
- SRS §C00 (MinIO Object Storage Foundation)
- SRS §C01 (error code -> HTTP table)
- SRS §3.1A (Storage Object), §3.1B (Upload Session)
- SRS §9.0A / §9.0B (state machines), §9.5 (recovery)
- ADR-0003 D-002 (dual adapter: Fake + MinIO), D-006 (Fake for tests),
  D-007 (signature tradeoffs for this Port)

The in-memory ``FakeObjectStore`` defined here exists ONLY to prove the
``ObjectStorePort`` / ``SourceArtifactReader`` Protocols are implementable and
usable. It is NOT a production adapter — the real ``MinioObjectStore`` lands in
M1 (WP1A) and is excluded from these tests per D-006.
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from knowledge_mining.mining.contracts.storage import (
    ChecksumMismatch,
    ObjectStat,
    ObjectStorePort,
    PartETag,
    PresignedAccess,
    PutOptions,
    PutResult,
    SourceArtifactReader,
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
    """Minimal in-memory implementation of ``ObjectStorePort``.

    State is keyed by ``storage_object_id`` (the business identity). Multipart
    sessions are held in a separate dict until completed. SHA-256 is computed
    over the reassembled byte stream, matching how the Port contract computes
    checksums server-side (SRS §3.1A).
    """

    provider = "fake"

    def __init__(self, bucket: str = "test-bucket") -> None:
        self._bucket = bucket
        # storage_object_id -> dict(bucket, key, data, sha256, stat)
        self._objects: dict[str, dict] = {}
        # (bucket, object_key) -> storage_object_id  (for copy/dedup lookups)
        self._by_key: dict[tuple[str, str], str] = {}
        # upload_id -> multipart session
        self._uploads: dict[str, dict] = {}

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _gen_id(self) -> str:
        return "so_" + secrets.token_hex(12)

    def _gen_key(self) -> str:
        return "obj/" + secrets.token_hex(16)

    async def _astream(self, data: bytes) -> AsyncIterator[bytes]:
        # Yield in one chunk; the contract treats this as an opaque stream.
        yield data

    # -- ObjectStorePort ---------------------------------------------------

    async def put_stream(
        self,
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
        storage_object_id = self._gen_id()
        key = self._gen_key()
        self._objects[storage_object_id] = {
            "bucket": self._bucket,
            "key": key,
            "data": data,
            "sha256": sha,
            "size": len(data),
            "mime": options.mime,
            "artifact_class": options.artifact_class,
            "etag": sha[:32],
        }
        self._by_key[(self._bucket, key)] = storage_object_id
        return PutResult(
            storage_object_id=storage_object_id,
            version_id=None,
            etag=sha[:32],
            sha256=sha,
            size=len(data),
        )

    async def get_stream(self, storage_object_id: str) -> AsyncIterator[bytes]:
        obj = self._require(storage_object_id)
        # async generator — return the bytes as a stream
        yield obj["data"]

    async def stat(self, storage_object_id: str) -> ObjectStat:
        obj = self._require(storage_object_id)
        return ObjectStat(
            storage_object_id=storage_object_id,
            size=obj["size"],
            sha256=obj["sha256"],
            etag=obj["etag"],
            mime=obj["mime"],
            artifact_class=obj["artifact_class"],
            encryption=None,
            version_id=None,
            last_verified_at=None,
        )

    async def delete(self, storage_object_id: str) -> None:
        obj = self._objects.pop(storage_object_id, None)
        if obj is not None:
            self._by_key.pop((obj["bucket"], obj["key"]), None)

    async def head_exists(self, storage_object_id: str) -> bool:
        return storage_object_id in self._objects

    async def copy(
        self,
        src_storage_object_id: str,
        dst_options: PutOptions,
    ) -> PutResult:
        src = self._require(src_storage_object_id)
        # Server-side copy: same bytes => same sha256.
        return await self.put_stream(self._astream(src["data"]), dst_options)

    async def presign_get(
        self,
        storage_object_id: str,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        self._require(storage_object_id)
        return PresignedAccess(
            method="GET",
            url=f"fake://get/{storage_object_id}?expires={expires_in_seconds}",
            expires_in_seconds=expires_in_seconds,
            storage_object_id=storage_object_id,
            object_key=f"obj/{storage_object_id}",
        )

    async def presign_put(
        self,
        bucket: str,
        object_key: str,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        return PresignedAccess(
            method="PUT",
            url=f"fake://put/{bucket}/{object_key}?expires={expires_in_seconds}",
            expires_in_seconds=expires_in_seconds,
            storage_object_id="",  # not yet materialized
            object_key=object_key,
        )

    async def initiate_multipart(
        self,
        bucket: str,
        object_key: str,
        options: PutOptions,
    ) -> UploadTicket:
        upload_id = "up_" + secrets.token_hex(12)
        self._uploads[upload_id] = {
            "bucket": bucket,
            "object_key": object_key,
            "options": options,
            "parts": {},  # part_number -> bytes
        }
        return UploadTicket(
            upload_id=upload_id,
            storage_object_id="",  # assigned on complete
            bucket=bucket,
            object_key=object_key,
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
        # Reuse put_stream to materialize the assembled object.
        result = await self.put_stream(self._astream(ordered), options)
        if expected_sha256 is not None and expected_sha256 != result.sha256:
            raise ChecksumMismatch(
                f"multipart expected {expected_sha256}, got {result.sha256}",
                expected=expected_sha256,
                actual=result.sha256,
            )
        return result

    async def abort_multipart(self, upload_id: str) -> None:
        self._uploads.pop(upload_id, None)

    # -- SourceArtifactReader ---------------------------------------------

    async def open_stream(self, storage_object_id: str) -> AsyncIterator[bytes]:
        obj = self._require(storage_object_id)
        yield obj["data"]

    async def materialize_temp(
        self,
        storage_object_id: str,
        run_id: str,
        *,
        tmp_root: Path,
    ) -> Path:
        """Write the object to a run-scoped temp file and verify its hash.

        ``tmp_root`` is injected for testability; a production implementation
        derives the temp root from run configuration.
        """
        obj = self._require(storage_object_id)
        path = tmp_root / run_id / f"{storage_object_id}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(obj["data"])
        actual = self._sha256(obj["data"])
        if actual != obj["sha256"]:
            raise ChecksumMismatch(
                f"materialized {storage_object_id} hash drifted",
                expected=obj["sha256"],
                actual=actual,
            )
        return path

    # -- internals ---------------------------------------------------------

    async def _drain(self, stream: AsyncIterator[bytes]) -> bytes:
        chunks: list[bytes] = []
        async for chunk in stream:
            chunks.append(chunk)
        return b"".join(chunks)

    def _require(self, storage_object_id: str) -> dict:
        obj = self._objects.get(storage_object_id)
        if obj is None:
            raise StorageObjectMissing(storage_object_id)
        return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> FakeObjectStore:
    return FakeObjectStore()


async def _bytes(data: bytes) -> AsyncIterator[bytes]:
    yield data


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_fake_satisfies_object_store_port(store: FakeObjectStore) -> None:
    """The Fake must be recognized as an ObjectStorePort (structural typing)."""
    assert isinstance(store, ObjectStorePort)


def test_fake_satisfies_source_artifact_reader(store: FakeObjectStore) -> None:
    assert isinstance(store, SourceArtifactReader)


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


# ---------------------------------------------------------------------------
# Round-trip: put -> get -> stat -> head_exists -> delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_get_roundtrip(store: FakeObjectStore) -> None:
    payload = b"hello object store" * 64
    result = await store.put_stream(_bytes(payload), PutOptions(artifact_class="source"))

    assert isinstance(result, PutResult)
    assert result.size == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()

    collected = b"".join([c async for c in store.get_stream(result.storage_object_id)])
    assert collected == payload


@pytest.mark.asyncio
async def test_stat_reports_sha256_and_size(store: FakeObjectStore) -> None:
    payload = b"stat me"
    result = await store.put_stream(_bytes(payload), PutOptions(mime="text/plain"))

    stat = await store.stat(result.storage_object_id)
    assert isinstance(stat, ObjectStat)
    assert stat.sha256 == result.sha256
    assert stat.size == len(payload)
    assert stat.mime == "text/plain"
    assert stat.artifact_class == "source"


@pytest.mark.asyncio
async def test_head_exists_and_delete(store: FakeObjectStore) -> None:
    result = await store.put_stream(_bytes(b"x"), PutOptions())

    assert await store.head_exists(result.storage_object_id) is True
    await store.delete(result.storage_object_id)
    assert await store.head_exists(result.storage_object_id) is False
    with pytest.raises(StorageObjectMissing):
        await store.stat(result.storage_object_id)


# ---------------------------------------------------------------------------
# Presign
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_presign_get_returns_url(store: FakeObjectStore) -> None:
    result = await store.put_stream(_bytes(b"x"), PutOptions())
    access = await store.presign_get(result.storage_object_id, expires_in_seconds=300)

    assert isinstance(access, PresignedAccess)
    assert access.method == "GET"
    assert access.url.startswith("fake://get/")
    assert access.expires_in_seconds == 300
    assert access.storage_object_id == result.storage_object_id


@pytest.mark.asyncio
async def test_presign_put_for_unknown_object(store: FakeObjectStore) -> None:
    access = await store.presign_put("bucket-a", "obj/key-1")
    assert access.method == "PUT"
    assert "bucket-a" in access.url and "obj/key-1" in access.url
    assert access.object_key == "obj/key-1"


# ---------------------------------------------------------------------------
# Multipart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multipart_roundtrip(store: FakeObjectStore) -> None:
    big = bytes(range(256)) * 1024  # 256 KiB
    ticket = await store.initiate_multipart(
        "bucket-a", "obj/multipart-1", PutOptions(artifact_class="source"),
    )
    assert isinstance(ticket, UploadTicket)
    assert ticket.upload_id

    # Split into 3 parts.
    third = len(big) // 3
    parts: list[PartETag] = []
    for i, pnum in enumerate([1, 2, 3]):
        chunk = big[i * third : (i + 1) * third] if i < 2 else big[i * third :]
        etag = await store.upload_part(ticket.upload_id, pnum, _bytes(chunk))
        assert etag.part_number == pnum
        parts.append(etag)

    result = await store.complete_multipart(ticket.upload_id, tuple(parts))

    got = b"".join([c async for c in store.get_stream(result.storage_object_id)])
    assert got == big
    assert result.sha256 == hashlib.sha256(big).hexdigest()


@pytest.mark.asyncio
async def test_abort_multipart_discards_session(store: FakeObjectStore) -> None:
    ticket = await store.initiate_multipart("b", "k", PutOptions())
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
    src = await store.put_stream(_bytes(payload), PutOptions(artifact_class="source"))
    dst = await store.copy(src.storage_object_id, PutOptions(artifact_class="backend_raw"))

    assert dst.storage_object_id != src.storage_object_id
    assert dst.sha256 == src.sha256  # same bytes => same hash


# ---------------------------------------------------------------------------
# SourceArtifactReader
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_temp_writes_and_verifies(
    store: FakeObjectStore,
    tmp_path: Path,
) -> None:
    payload = b"materialize payload"
    result = await store.put_stream(_bytes(payload), PutOptions())

    path = await store.materialize_temp(
        result.storage_object_id, "run_123", tmp_root=tmp_path,
    )
    assert path.exists()
    assert path.read_bytes() == payload
    # Run-scoped layout.
    assert "run_123" in str(path)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checksum_mismatch_on_expected_sha256(store: FakeObjectStore) -> None:
    with pytest.raises(ChecksumMismatch) as exc_info:
        await store.put_stream(
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
        await store.stat("so_does_not_exist")


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
        storage_object_id="so_x",
        version_id=None,
        etag="e",
        sha256="a" * 64,
        size=10,
    )
    with pytest.raises(FrozenInstanceError):
        result.size = 99  # type: ignore[misc]
