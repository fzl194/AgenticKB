"""``ObjectStoreSourceArtifactReader`` — stream + materialize frozen bytes.

Fulfills the D-020 commitment that ``SourceArtifactReader`` is implemented
as a Repository+ObjectStore composition at the application layer (the Port
was changed to location addressing and no longer carries the business
``storage_object_id``). The reader takes a ``FrozenInput`` (which already
carries the resolved location captured by ``FrozenInputService.freeze``),
opens the object stream via ``ObjectStorePort.get_stream``, and verifies the
streamed sha256 against ``frozen.source_raw_hash`` on the fly.

Two access modes (SRS §C00 / §10.2, ADR-0003 D-024):

- ``open_stream`` — async generator yielding the object bytes for parsers
  that can consume a stream directly. Hash is verified incrementally; a
  mismatch raises ``StorageObjectCorrupt`` and the generator stops.
- ``materialize_temp`` — writes the object bytes to a run-scoped temporary
  directory ``{tmp_root}/{run_id}/{storage_object_id}`` for third-party
  parsers that require a local file path. Hash is verified after the write;
  on mismatch the partial file is removed and ``StorageObjectCorrupt`` is
  raised. The temp path is NOT a persisted asset field (SRS §10.2) — callers
  MUST ``cleanup_temp(run_id)`` when the run ends (success / failure /
  cancel / recovery).

Design:
- The reader does NOT re-resolve the storage object id through the
  Repository on every read: the ``FrozenInput`` snapshot is authoritative
  for the run's lifetime, and staleness is checked separately by
  ``FrozenInputService.check_stale``. This keeps the hot path (streaming
  bytes) free of repo round-trips.
- Streaming hash uses ``hashlib.sha256`` updated per chunk; the digest is
  compared to ``source_raw_hash`` after the stream is fully consumed.
- No async file IO library — blocking writes go through ``asyncio.to_thread``
  to stay off the event loop (consistent with ``FakeObjectStore``, D-021).
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from knowledge_mining.mining.contracts.storage.errors import (
    StorageObjectCorrupt,
)
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import ObjectLocation
from knowledge_mining.mining.frozen_input.contracts import FrozenInput

#: Streaming chunk size for both network reads and disk writes (64 KiB,
#: matches FakeObjectStore).
_CHUNK_SIZE = 64 * 1024


class ObjectStoreSourceArtifactReader:
    """Stream or materialize the bytes of a frozen Storage Object (D-020).

    Stateless across runs; the only instance state is the injected
    ``ObjectStorePort`` and the temp-root directory. Safe to share.
    """

    __slots__ = ("_object_store", "_tmp_root")

    def __init__(self, object_store: ObjectStorePort, tmp_root: Path) -> None:
        self._object_store = object_store
        self._tmp_root = Path(tmp_root)
        # Eagerly create the root so run-dir creation is a single mkdir.
        self._tmp_root.mkdir(parents=True, exist_ok=True)

    # -- location ----------------------------------------------------------

    @staticmethod
    def _location(frozen: FrozenInput) -> ObjectLocation:
        """Build the ``ObjectLocation`` captured in the frozen binding."""
        return ObjectLocation(
            bucket=frozen.bucket,
            object_key=frozen.object_key,
            version_id=frozen.object_version_id,
        )

    @staticmethod
    def _verify_hash(
        storage_object_id: str, expected: str, actual: str
    ) -> None:
        """Raise ``StorageObjectCorrupt`` if ``actual != expected``."""
        if actual != expected:
            raise StorageObjectCorrupt(
                storage_object_id,
                f"sha256 mismatch for {storage_object_id!r}: "
                f"expected {expected}, got {actual}",
            )

    # -- streaming read ----------------------------------------------------

    async def open_stream(
        self, frozen: FrozenInput
    ) -> AsyncIterator[bytes]:
        """Yield the frozen object's bytes, verifying sha256 on the fly.

        Raises ``StorageObjectCorrupt`` (via the post-stream digest check) if
        the object bytes drift from the frozen hash. The generator-style
        ``async def`` + ``yield`` makes this an ``AsyncIterator`` directly;
        callers do ``async for chunk in reader.open_stream(frozen): ...``.
        """
        location = self._location(frozen)
        digest = hashlib.sha256()
        # ``get_stream`` is an ``async def`` generator: calling it returns an
        # async iterator directly (no await) — the body runs lazily on the
        # first ``__anext__``. See FakeObjectStore.get_stream / test usage.
        stream = self._object_store.get_stream(location)
        async for chunk in stream:
            digest.update(chunk)
            yield chunk
        self._verify_hash(
            frozen.source_storage_object_id,
            frozen.source_raw_hash,
            digest.hexdigest(),
        )

    # -- materialize to temp ----------------------------------------------

    async def materialize_temp(
        self, frozen: FrozenInput, run_id: str
    ) -> Path:
        """Write the frozen object bytes to a run-scoped temp file (SRS §10.2).

        Layout: ``{tmp_root}/{run_id}/{storage_object_id}``. Returns the
        absolute path; the temp file is NOT registered as an asset (SRS
        §10.2: "临时路径不是资产字段"). Hash is verified after the write; on
        mismatch the file is removed and ``StorageObjectCorrupt`` is raised.
        Callers MUST call ``cleanup_temp(run_id)`` when the run ends.
        """
        target = self._run_dir(run_id) / frozen.source_storage_object_id
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        digest = await self._write_stream_to_file(frozen, target)
        try:
            self._verify_hash(
                frozen.source_storage_object_id,
                frozen.source_raw_hash,
                digest,
            )
        except StorageObjectCorrupt:
            # Remove the partial file so a retry does not pick up bad bytes.
            await asyncio.to_thread(_safe_unlink, target)
            raise
        return target

    async def _write_stream_to_file(
        self, frozen: FrozenInput, target: Path
    ) -> str:
        """Drain the frozen stream into ``target``, returning the sha256 hex."""
        location = self._location(frozen)
        stream = self._object_store.get_stream(location)
        digest = hashlib.sha256()
        # Buffer chunks in memory only across the await boundary; write in a
        # thread to avoid blocking the loop on each chunk.
        fd_holder: dict[str, object] = {}

        def _open() -> None:
            fd_holder["fh"] = open(target, "wb")  # noqa: SIM115 - closed in _close

        await asyncio.to_thread(_open)
        fh = fd_holder["fh"]  # type: ignore[assignment]
        try:
            async for chunk in stream:
                digest.update(chunk)
                await asyncio.to_thread(fh.write, chunk)
        finally:
            await asyncio.to_thread(fh.close)
        return digest.hexdigest()

    # -- cleanup -----------------------------------------------------------

    def _run_dir(self, run_id: str) -> Path:
        """Return ``{tmp_root}/{run_id}`` (created lazily by callers)."""
        return self._tmp_root / run_id

    async def cleanup_temp(self, run_id: str) -> None:
        """Remove the run-scoped temp directory (SRS §10.2).

        Safe to call multiple times / when nothing was materialized: missing
        directories are ignored (``ignore_errors``).
        """
        run_dir = self._run_dir(run_id)
        await asyncio.to_thread(shutil.rmtree, run_dir, ignore_errors=True)


def _safe_unlink(path: Path) -> None:
    """``unlink`` ignoring missing-file errors (helper for thread executor)."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


__all__ = ["ObjectStoreSourceArtifactReader"]
