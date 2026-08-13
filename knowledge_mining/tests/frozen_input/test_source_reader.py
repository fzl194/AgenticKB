"""Tests for ``ObjectStoreSourceArtifactReader`` (M1.4, WP1D).

Covers SRS §C00 (SourceArtifactReader open_stream/materialize_temp),
§10.2 (parser only consumes frozen objects; temp paths are not asset fields),
§9.5 (corruption detection):

- ``open_stream`` round-trips bytes and passes the sha256 check.
- ``open_stream`` raises ``StorageObjectCorrupt`` when the object bytes drift
  from the frozen hash (simulated by freezing hash A then swapping the
  object bytes to hash B).
- ``materialize_temp`` writes the bytes to ``{tmp_root}/{run_id}/{so_id}``,
  verifies sha256, and ``cleanup_temp`` removes the run dir.
- ``materialize_temp`` raises on hash mismatch and leaves no partial file.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

# psycopg-async needs the SelectorEventLoop on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.storage.errors import (  # noqa: E402
    StorageObjectCorrupt,
)
from knowledge_mining.mining.contracts.storage.types import (  # noqa: E402
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.frozen_input.contracts import FrozenInput  # noqa: E402
from knowledge_mining.mining.frozen_input.source_reader import (  # noqa: E402
    ObjectStoreSourceArtifactReader,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _frozen(
    *,
    data: bytes,
    storage_object_id: str = "so_1",
    bucket: str = "kb1-source",
    object_key: str | None = None,
    raw_hash_override: str | None = None,
) -> FrozenInput:
    sha = raw_hash_override or _sha256(data)
    key = object_key or f"v1/ab/cd/{sha}"
    return FrozenInput(
        document_id="doc1",
        source_storage_object_id=storage_object_id,
        source_raw_hash=sha,
        source_content_revision=1,
        mime="text/plain",
        size=len(data),
        original_filename="doc1.txt",
        captured_at="2026-08-13T00:00:00+00:00",
        provider="fake",
        bucket=bucket,
        object_key=key,
        object_version_id=None,
    )


@pytest_asyncio.fixture
async def reader_and_store(
    tmp_path,
) -> tuple[ObjectStoreSourceArtifactReader, FakeObjectStore]:
    store = FakeObjectStore(str(tmp_path / "store_root"))
    reader = ObjectStoreSourceArtifactReader(store, tmp_path / "tmp_root")
    return reader, store


async def _put(store: FakeObjectStore, location: ObjectLocation, data: bytes) -> None:
    await store.put_bytes(
        location,
        data,
        PutOptions(artifact_class="source", mime="text/plain"),
    )


# ---------------------------------------------------------------------------
# open_stream
# ---------------------------------------------------------------------------


async def test_open_stream_round_trips_and_verifies(
    reader_and_store: tuple[ObjectStoreSourceArtifactReader, FakeObjectStore],
) -> None:
    reader, store = reader_and_store
    data = b"streaming source artifact bytes " * 100
    frozen = _frozen(data=data)
    await _put(store, ObjectLocation(frozen.bucket, frozen.object_key), data)

    collected = b""
    async for chunk in reader.open_stream(frozen):
        collected += chunk

    assert collected == data
    assert _sha256(collected) == frozen.source_raw_hash


async def test_open_stream_detects_corruption(
    reader_and_store: tuple[ObjectStoreSourceArtifactReader, FakeObjectStore],
) -> None:
    reader, store = reader_and_store
    # Freeze hash A, but write object bytes that hash to B.
    real_bytes = b"the real content"
    frozen = _frozen(data=real_bytes)
    tampered = b"the REAL content (tampered)"  # different bytes, different hash
    await _put(
        store, ObjectLocation(frozen.bucket, frozen.object_key), tampered
    )

    with pytest.raises(StorageObjectCorrupt):
        async for _ in reader.open_stream(frozen):
            pass


async def test_open_stream_small_file_round_trips(
    reader_and_store: tuple[ObjectStoreSourceArtifactReader, FakeObjectStore],
) -> None:
    reader, store = reader_and_store
    data = b"x"  # single byte, exercises chunking edge case
    frozen = _frozen(data=data)
    await _put(store, ObjectLocation(frozen.bucket, frozen.object_key), data)

    got = b"".join(
        [c async for c in reader.open_stream(frozen)]
    )
    assert got == data


# ---------------------------------------------------------------------------
# materialize_temp
# ---------------------------------------------------------------------------


async def test_materialize_temp_writes_run_scoped_file_and_verifies(
    reader_and_store: tuple[ObjectStoreSourceArtifactReader, FakeObjectStore],
    tmp_path,
) -> None:
    reader, store = reader_and_store
    data = b"materialize me " * 50
    frozen = _frozen(data=data)
    await _put(store, ObjectLocation(frozen.bucket, frozen.object_key), data)

    path = await reader.materialize_temp(frozen, run_id="run_42")

    assert isinstance(path, Path)
    assert path.read_bytes() == data
    # Run-scoped layout: {tmp_root}/{run_id}/{storage_object_id}
    assert path.parent.name == "run_42"
    assert path.name == "so_1"
    assert str(path).startswith(str(tmp_path / "tmp_root"))


async def test_materialize_temp_cleanup_removes_run_dir(
    reader_and_store: tuple[ObjectStoreSourceArtifactReader, FakeObjectStore],
    tmp_path,
) -> None:
    reader, store = reader_and_store
    data = b"cleanup target"
    frozen = _frozen(data=data)
    await _put(store, ObjectLocation(frozen.bucket, frozen.object_key), data)

    path = await reader.materialize_temp(frozen, run_id="run_43")
    assert path.exists()

    await reader.cleanup_temp("run_43")

    assert not path.exists()
    assert not (tmp_path / "tmp_root" / "run_43").exists()


async def test_materialize_temp_cleanup_idempotent_when_absent(
    reader_and_store: tuple[ObjectStoreSourceArtifactReader, FakeObjectStore],
) -> None:
    reader, _ = reader_and_store
    # Cleanup a run that was never materialized — should not raise.
    await reader.cleanup_temp("run_never")


async def test_materialize_temp_detects_corruption_and_removes_partial(
    reader_and_store: tuple[ObjectStoreSourceArtifactReader, FakeObjectStore],
) -> None:
    reader, store = reader_and_store
    real_bytes = b"honest bytes"
    frozen = _frozen(data=real_bytes)
    tampered = b"dishonest bytes"  # different hash
    await _put(
        store, ObjectLocation(frozen.bucket, frozen.object_key), tampered
    )

    with pytest.raises(StorageObjectCorrupt):
        await reader.materialize_temp(frozen, run_id="run_corrupt")

    # Partial file must have been removed.
    leftover = (
        Path(reader._tmp_root)  # noqa: SLF001 - test-only reach
        / "run_corrupt"
        / frozen.source_storage_object_id
    )
    assert not leftover.exists()


async def test_materialize_temp_multiple_runs_isolated(
    reader_and_store: tuple[ObjectStoreSourceArtifactReader, FakeObjectStore],
) -> None:
    reader, store = reader_and_store
    data = b"shared content"
    frozen = _frozen(data=data)
    await _put(store, ObjectLocation(frozen.bucket, frozen.object_key), data)

    p_a = await reader.materialize_temp(frozen, run_id="run_a")
    p_b = await reader.materialize_temp(frozen, run_id="run_b")

    assert p_a.parent.name == "run_a"
    assert p_b.parent.name == "run_b"
    assert p_a != p_b

    # Cleaning up run_a must not affect run_b.
    await reader.cleanup_temp("run_a")
    assert not p_a.exists()
    assert p_b.exists()
