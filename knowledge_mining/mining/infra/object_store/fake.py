"""Filesystem-backed ``ObjectStorePort`` (M1.1, WP1A; ADR-0003 D-002, D-006).

``FakeObjectStore`` persists objects under ``{root}/{bucket}/{object_key}`` and
sidecar metadata under ``{root}/{bucket}/{object_key}.meta.json``. Because state
is on disk, two instances pointing at the same ``root`` see each other's writes
(cross-instance persistence) — this is what lets unit tests construct a fresh
adapter and still read what an earlier instance produced.

Blocking filesystem IO is wrapped with ``asyncio.to_thread`` so the Port stays
fully async without pulling in aiofiles (no new dependency).

Layout:
  {root}/{bucket}/{object_key}           -> object bytes
  {root}/{bucket}/{object_key}.meta.json -> sha256/size/etag/mime/...
  {root}/_multipart/{upload_id}/         -> multipart session dir
      target.json                        -> chosen ObjectLocation + PutOptions
      part.{n}                           -> bytes of part n
      part.{n}.etag                      -> md5 etag of part n

References:
- SRS §C00 (MinIO Object Storage Foundation)
- SRS §3.1A (Storage Object), §3.1B (Upload Session), §9.5 (recovery)
- SRS §8.1 (key layout — applied by caller, not here)
- ADR-0003 D-002 (dual adapter), D-006 (Fake for tests), D-020 (location)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

from knowledge_mining.mining.contracts.storage.enums import VALID_ARTIFACT_CLASSES
from knowledge_mining.mining.contracts.storage.errors import (
    ChecksumMismatch,
    StorageObjectMissing,
)
from knowledge_mining.mining.contracts.storage.types import (
    ObjectLocation,
    ObjectStat,
    PartETag,
    PresignedAccess,
    PutOptions,
    PutResult,
    UploadTicket,
)

_CHUNK_SIZE = 64 * 1024  # 64 KiB streaming chunks
_META_SUFFIX = ".meta.json"
_MULTIPART_DIR = "_multipart"


class FakeObjectStore:
    """Filesystem-backed ``ObjectStorePort`` (D-002, D-006, D-020)."""

    provider = "fake"

    def __init__(self, root_path: str) -> None:
        self._root = Path(root_path)
        # Eagerly create the root so existence checks behave consistently.
        self._root.mkdir(parents=True, exist_ok=True)

    # -- path helpers ------------------------------------------------------

    def _object_path(self, location: ObjectLocation) -> Path:
        return self._root / location.bucket / location.object_key

    def _upload_dir(self, upload_id: str) -> Path:
        return self._root / _MULTIPART_DIR / upload_id

    # -- ObjectStorePort: put / get / stat / delete / exists ---------------

    async def put_stream(
        self,
        location: ObjectLocation,
        stream: AsyncIterator[bytes],
        options: PutOptions,
    ) -> PutResult:
        if options.artifact_class not in VALID_ARTIFACT_CLASSES:
            raise ValueError(f"unknown artifact_class: {options.artifact_class}")
        # Drain the async stream on the loop first (no blocking inside a thread),
        # then perform the blocking filesystem write off-loop.
        data = await _drain(stream)
        sha, size = await asyncio.to_thread(
            self._write_object, location, data, options
        )
        return PutResult(version_id=None, etag=sha[:32], sha256=sha, size=size)

    async def put_bytes(
        self,
        location: ObjectLocation,
        data: bytes,
        options: PutOptions,
    ) -> PutResult:
        """Convenience: put a materialized ``bytes`` buffer (used by tests).

        Computes sha256, validates ``expected_sha256`` (fail-closed), writes
        atomically and records the sidecar meta.
        """
        if options.artifact_class not in VALID_ARTIFACT_CLASSES:
            raise ValueError(f"unknown artifact_class: {options.artifact_class}")
        sha, size = await asyncio.to_thread(self._write_object, location, data, options)
        return PutResult(version_id=None, etag=sha[:32], sha256=sha, size=size)

    def _write_object(
        self,
        location: ObjectLocation,
        data: bytes,
        options: PutOptions,
    ) -> tuple[str, int]:
        sha = hashlib.sha256(data).hexdigest()
        if options.expected_sha256 is not None and options.expected_sha256 != sha:
            raise ChecksumMismatch(
                f"expected {options.expected_sha256}, got {sha}",
                expected=options.expected_sha256,
                actual=sha,
            )
        target = self._object_path(location)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tmp -> rename.
        fd, tmp = tempfile.mkstemp(prefix=".put-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        size = len(data)
        self._write_meta(location, sha, size, options)
        return sha, size

    def _write_meta(
        self,
        location: ObjectLocation,
        sha: str,
        size: int,
        options: PutOptions,
    ) -> None:
        meta = {
            "sha256": sha,
            "size": size,
            "etag": sha[:32],
            "mime": options.mime,
            "artifact_class": options.artifact_class,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path = self._meta_path_for(location)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    def _meta_path_for(self, location: ObjectLocation) -> Path:
        # Sidecar sits next to the object: <key>.meta.json (append, do not
        # replace any real suffix in the key).
        return self._object_path(location).with_name(
            self._object_path(location).name + _META_SUFFIX
        )

    async def get_stream(self, location: ObjectLocation) -> AsyncIterator[bytes]:
        path = self._object_path(location)
        if not path.is_file():
            raise StorageObjectMissing(f"{location.bucket}/{location.object_key}")
        # Read in chunks via a thread to avoid blocking the loop on large IO.
        chunk = _CHUNK_SIZE
        offset = 0
        with path.open("rb") as fh:
            while True:
                data = await asyncio.to_thread(fh.read, chunk)
                if not data:
                    break
                yield data
                offset += len(data)

    async def stat(self, location: ObjectLocation) -> ObjectStat:
        path = self._object_path(location)
        meta = await asyncio.to_thread(self._read_meta, location)
        if meta is None or not path.is_file():
            raise StorageObjectMissing(f"{location.bucket}/{location.object_key}")
        return ObjectStat(
            bucket=location.bucket,
            object_key=location.object_key,
            size=meta["size"],
            sha256=meta["sha256"],
            etag=meta.get("etag"),
            mime=meta.get("mime"),
            artifact_class=meta.get("artifact_class", "source"),
            encryption=None,
            version_id=None,
            last_verified_at=None,
        )

    async def delete(self, location: ObjectLocation) -> None:
        def _rm() -> None:
            obj = self._object_path(location)
            meta = self._meta_path_for(location)
            for p in (obj, meta):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass

        await asyncio.to_thread(_rm)

    async def head_exists(self, location: ObjectLocation) -> bool:
        return await asyncio.to_thread(lambda: self._object_path(location).is_file())

    async def copy(
        self,
        src: ObjectLocation,
        dst: ObjectLocation,
        options: PutOptions,
    ) -> PutResult:
        src_path = self._object_path(src)
        if not await asyncio.to_thread(src_path.is_file):
            raise StorageObjectMissing(f"{src.bucket}/{src.object_key}")
        data = await asyncio.to_thread(src_path.read_bytes)
        return await self.put_bytes(dst, data, options)

    # -- Presign -----------------------------------------------------------

    async def presign_get(
        self,
        location: ObjectLocation,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        return PresignedAccess(
            method="GET",
            url=self._fake_url("GET", location, expires_in_seconds),
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
            url=self._fake_url("PUT", location, expires_in_seconds),
            expires_in_seconds=expires_in_seconds,
            location=location,
        )

    @staticmethod
    def _fake_url(method: str, location: ObjectLocation, expires: int) -> str:
        return (
            f"fake://{location.bucket}/{location.object_key}"
            f"?method={method}&expires={expires}"
        )

    # -- Multipart ---------------------------------------------------------

    async def initiate_multipart(
        self,
        location: ObjectLocation,
        options: PutOptions,
    ) -> UploadTicket:
        upload_id = "up_" + secrets.token_hex(12)

        def _setup() -> None:
            ud = self._upload_dir(upload_id)
            ud.mkdir(parents=True, exist_ok=True)
            (ud / "target.json").write_text(
                json.dumps(
                    {
                        "bucket": location.bucket,
                        "object_key": location.object_key,
                        "version_id": location.version_id,
                        "options": _options_to_json(options),
                    }
                ),
                encoding="utf-8",
            )

        await asyncio.to_thread(_setup)
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
        data = await _drain(stream)
        ud = self._upload_dir(upload_id)
        if not ud.is_dir():
            raise StorageObjectMissing(f"upload {upload_id}")

        def _write_part() -> str:
            (ud / f"part.{part_number}").write_bytes(data)
            etag = hashlib.md5(data).hexdigest()
            (ud / f"part.{part_number}.etag").write_text(etag, encoding="utf-8")
            return etag

        etag = await asyncio.to_thread(_write_part)
        return PartETag(part_number=part_number, etag=etag)

    async def complete_multipart(
        self,
        upload_id: str,
        parts: tuple[PartETag, ...],
        expected_sha256: str | None = None,
    ) -> PutResult:
        ud = self._upload_dir(upload_id)
        if not ud.is_dir():
            raise StorageObjectMissing(f"upload {upload_id}")

        target, options, data = await asyncio.to_thread(
            self._assemble, ud, parts
        )
        sha, size = await asyncio.to_thread(
            self._write_object, target, data, options
        )
        if expected_sha256 is not None and expected_sha256 != sha:
            # Discard the assembled object on mismatch (SRS §C01).
            await asyncio.to_thread(self._object_path(target).unlink, True)
            raise ChecksumMismatch(
                f"multipart expected {expected_sha256}, got {sha}",
                expected=expected_sha256,
                actual=sha,
            )
        await asyncio.to_thread(shutil.rmtree, ud, ignore_errors=True)
        return PutResult(version_id=None, etag=sha[:32], sha256=sha, size=size)

    def _assemble(
        self,
        ud: Path,
        parts: tuple[PartETag, ...],
    ) -> tuple[ObjectLocation, PutOptions, bytes]:
        spec = json.loads((ud / "target.json").read_text(encoding="utf-8"))
        location = ObjectLocation(
            bucket=spec["bucket"],
            object_key=spec["object_key"],
            version_id=spec.get("version_id"),
        )
        options = _options_from_json(spec["options"])
        ordered = sorted(parts, key=lambda p: p.part_number)
        chunks: list[bytes] = []
        for p in ordered:
            part_path = ud / f"part.{p.part_number}"
            if not part_path.is_file():
                raise StorageObjectMissing(
                    f"part {p.part_number} of upload {ud.name}"
                )
            chunks.append(part_path.read_bytes())
        return location, options, b"".join(chunks)

    async def abort_multipart(self, upload_id: str) -> None:
        ud = self._upload_dir(upload_id)
        await asyncio.to_thread(shutil.rmtree, ud, ignore_errors=True)

    # -- meta IO -----------------------------------------------------------

    def _read_meta(self, location: ObjectLocation) -> dict | None:
        meta_path = self._meta_path_for(location)
        if not meta_path.is_file():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _drain(stream: AsyncIterator[bytes]) -> bytes:
    chunks: list[bytes] = []
    async for chunk in stream:
        chunks.append(chunk)
    return b"".join(chunks)


def _options_to_json(options: PutOptions) -> dict:
    return {
        "artifact_class": options.artifact_class,
        "mime": options.mime,
        "expected_sha256": options.expected_sha256,
        "content_length": options.content_length,
        "metadata": dict(options.metadata),
    }


def _options_from_json(d: dict) -> PutOptions:
    return PutOptions(
        artifact_class=d.get("artifact_class", "source"),
        mime=d.get("mime"),
        expected_sha256=d.get("expected_sha256"),
        content_length=d.get("content_length"),
        metadata=dict(d.get("metadata") or {}),
    )


__all__ = ["FakeObjectStore"]
