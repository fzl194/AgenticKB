"""MinIO / S3-backed ``ObjectStorePort`` (M1.1, WP1A; ADR-0003 D-002, D-006).

``MinioObjectStore`` wraps the synchronous ``minio`` Python SDK. The SDK is
imported lazily inside ``__init__`` and methods, so this module imports cleanly
even when ``minio`` is not installed — only actually constructing the adapter
(or running a guarded smoke test) requires the dependency (D-006).

Design notes:
- Blocking SDK calls run via ``asyncio.to_thread`` to keep the Port async
  without introducing aiohttp/aiofiles dependencies.
- Large GET responses are buffered to a run-scoped temp file then streamed back
  in chunks, avoiding unbounded memory for big objects.
- All SDK exceptions are normalized through ``_map_s3_error`` so raw
  ``S3Error`` never crosses the adapter boundary (SRS §C00).

References:
- SRS §C00 (MinIO Object Storage Foundation)
- SRS §3.1A / §3.1B (Storage Object / Upload Session)
- SRS §8.1 (bucket_prefix + artifact class), §8.7, §8.9
- ADR-0003 D-002 (dual adapter), D-005 (versioning), D-006 (guarded smoke),
  D-020 (ObjectLocation addressing)
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from typing import Any

from knowledge_mining.mining.contracts.storage.errors import (
    ChecksumMismatch,
    StorageError,
    StorageForbidden,
    StorageObjectMissing,
    StorageUnavailable,
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
from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig

_CHUNK_SIZE = 64 * 1024  # 64 KiB streaming chunks

# S3 error codes we recognize. Anything else falls back to StorageUnavailable
# (transient/unknown) per SRS §9.5 — never masquerade as a 404.
_S3_CODE_MAP: dict[str, type[StorageError]] = {
    "NoSuchKey": StorageObjectMissing,
    "NoSuchBucket": StorageObjectMissing,
    "AccessDenied": StorageForbidden,
    "Forbidden": StorageForbidden,
    "InvalidAccessKeyId": StorageForbidden,
    "SignatureDoesNotMatch": StorageForbidden,
}


# ---------------------------------------------------------------------------
# Pure helpers (no minio import — unit-testable in isolation)
# ---------------------------------------------------------------------------


def _map_s3_error(exc: BaseException) -> StorageError:
    """Map an SDK / S3 exception to a project ``StorageError``.

    Duck-typed on ``exc.response_code`` / ``exc.code`` (minio ``S3Error``
    exposes both); unknown codes become ``StorageUnavailable`` (retryable)
    rather than ``StorageObjectMissing`` (SRS §9.5 — never mask as 404).
    """
    code = _extract_code(exc)
    mapped_cls = _S3_CODE_MAP.get(code) if code else None
    if mapped_cls is StorageObjectMissing:
        return StorageObjectMissing(f"s3 code={code}: {exc}")
    if mapped_cls is StorageForbidden:
        return StorageForbidden(f"s3 code={code}: {exc}")
    # Default: transient/unknown -> retryable (SRS §9.5).
    return StorageUnavailable(f"s3 code={code or 'unknown'}: {exc}")


def _extract_code(exc: BaseException) -> str | None:
    for attr in ("response_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def _bucket_for(config: ObjectStoreConfig, artifact_class: str) -> str:
    """Bucket name for an artifact class (SRS §8.1): ``{prefix}{artifact_class}``."""
    return f"{config.bucket_prefix}{artifact_class}"


class MinioObjectStore:
    """Production ``ObjectStorePort`` over the MinIO SDK (D-002, D-006, D-020).

    ``minio`` is imported lazily in ``__init__``; importing this module does
    not require the dependency. Construction builds the SDK client but does not
    contact the server — call ``ensure_buckets`` (guarded) to provision buckets.
    """

    provider = "minio"

    def __init__(self, config: ObjectStoreConfig) -> None:
        if config.provider != "minio":
            raise ValueError(f"MinioObjectStore requires provider='minio', got {config.provider!r}")
        # Lazy import: keep this OUT of module top-level (D-006).
        try:
            from minio import Minio  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised when minio absent
            raise ImportError(
                "MinioObjectStore requires the 'minio' package; "
                "install with `pip install minio>=7.2`"
            ) from exc
        self._config = config
        self._client: Any = Minio(
            config.endpoint,
            access_key=config.access_key or None,
            secret_key=config.secret_key or None,
            secure=config.secure,
            region=config.region,
        )

    # -- bucket provisioning (guarded smoke only) --------------------------

    async def ensure_buckets(self, artifact_classes: tuple[str, ...] = ()) -> None:
        """Create buckets for each artifact class if missing (SRS §8.1, D-005).

        Enables versioning on each bucket (D-005 / O5). Intended to be called
        from a guarded smoke / bootstrap path, not the hot request path.
        """
        classes = artifact_classes or ("source", "parse", "binary", "staging")
        for cls_name in classes:
            bucket = _bucket_for(self._config, cls_name)
            await asyncio.to_thread(self._ensure_bucket, bucket)

    def _ensure_bucket(self, bucket: str) -> None:
        try:
            from minio.error import S3Error  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover
            S3Error = Exception  # type: ignore[assignment, misc]
        try:
            if not self._client.bucket_exists(bucket):
                self._client.make_bucket(bucket)
            # Best-effort versioning enable (D-005). minio <7.1 lacks the helper.
            try:
                self._client.enable_versioning(bucket)
            except Exception:  # noqa: BLE001 - versioning is best-effort
                pass
        except S3Error as exc:  # type: ignore[misc]
            raise _map_s3_error(exc) from exc

    # -- put / get / stat / delete / exists --------------------------------

    async def put_stream(
        self,
        location: ObjectLocation,
        stream: AsyncIterator[bytes],
        options: PutOptions,
    ) -> PutResult:
        # Materialize to a tmp file so we can both stream into minio's
        # blocking put_object and compute sha256 in one pass.
        data = await _drain(stream)
        sha = hashlib.sha256(data).hexdigest()
        if options.expected_sha256 is not None and options.expected_sha256 != sha:
            raise ChecksumMismatch(
                f"expected {options.expected_sha256}, got {sha}",
                expected=options.expected_sha256,
                actual=sha,
            )
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".minio-put-")
        try:
            with os.fdopen(tmp_fd, "wb") as fh:
                fh.write(data)
            result = await asyncio.to_thread(
                self._put_file_blocking, location, tmp_path, options, len(data), sha
            )
            return result
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _put_file_blocking(
        self,
        location: ObjectLocation,
        tmp_path: str,
        options: PutOptions,
        size: int,
        sha: str,
    ) -> PutResult:
        try:
            from minio.error import S3Error  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover
            S3Error = Exception  # type: ignore[assignment, misc]
        try:
            resp = self._client.put_object(
                location.bucket,
                location.object_key,
                _FileReader(tmp_path),
                length=size,
                content_type=options.mime or "application/octet-stream",
                metadata=dict(options.metadata) or None,
            )
            version_id = getattr(resp, "version_id", None)
            etag = getattr(resp, "etag", None)
            return PutResult(version_id=version_id, etag=etag, sha256=sha, size=size)
        except S3Error as exc:  # type: ignore[misc]
            raise _map_s3_error(exc) from exc

    async def get_stream(self, location: ObjectLocation) -> AsyncIterator[bytes]:
        # Buffer the response to a tmp file off-loop, then stream chunks back.
        # This avoids holding the SDK response object across awaits.
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".minio-get-")
        os.close(tmp_fd)
        try:
            await asyncio.to_thread(self._get_to_file_blocking, location, tmp_path)
            with open(tmp_path, "rb") as fh:
                while True:
                    chunk = await asyncio.to_thread(fh.read, _CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _get_to_file_blocking(self, location: ObjectLocation, tmp_path: str) -> None:
        try:
            from minio.error import S3Error  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover
            S3Error = Exception  # type: ignore[assignment, misc]
        try:
            resp = self._client.get_object(
                location.bucket, location.object_key, version_id=location.version_id
            )
            try:
                with open(tmp_path, "wb") as fh:
                    for chunk in resp.stream(_CHUNK_SIZE):
                        fh.write(chunk)
            finally:
                resp.close()
                resp.release_conn()
        except S3Error as exc:  # type: ignore[misc]
            raise _map_s3_error(exc) from exc

    async def stat(self, location: ObjectLocation) -> ObjectStat:
        return await asyncio.to_thread(self._stat_blocking, location)

    def _stat_blocking(self, location: ObjectLocation) -> ObjectStat:
        try:
            from minio.error import S3Error  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover
            S3Error = Exception  # type: ignore[assignment, misc]
        try:
            meta = self._client.stat_object(
                location.bucket, location.object_key, version_id=location.version_id
            )
        except S3Error as exc:  # type: ignore[misc]
            raise _map_s3_error(exc) from exc
        size = int(getattr(meta, "size", 0))
        return ObjectStat(
            bucket=location.bucket,
            object_key=location.object_key,
            size=size,
            sha256=_meta_get(meta, "sha256"),
            etag=getattr(meta, "etag", None),
            mime=getattr(meta, "content_type", None),
            artifact_class="source",
            encryption=None,
            version_id=getattr(meta, "version_id", None),
            last_verified_at=None,
        )

    async def delete(self, location: ObjectLocation) -> None:
        await asyncio.to_thread(self._delete_blocking, location)

    def _delete_blocking(self, location: ObjectLocation) -> None:
        try:
            from minio.error import S3Error  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover
            S3Error = Exception  # type: ignore[assignment, misc]
        try:
            self._client.remove_object(
                location.bucket, location.object_key, version_id=location.version_id
            )
        except S3Error as exc:  # type: ignore[misc]
            raise _map_s3_error(exc) from exc

    async def head_exists(self, location: ObjectLocation) -> bool:
        try:
            await asyncio.to_thread(self._stat_blocking, location)
            return True
        except StorageObjectMissing:
            return False

    async def copy(
        self,
        src: ObjectLocation,
        dst: ObjectLocation,
        options: PutOptions,
    ) -> PutResult:
        # S3 server-side copy preserves bytes => same sha256 as source.
        src_stat = await asyncio.to_thread(self._stat_blocking, src)
        await asyncio.to_thread(self._copy_blocking, src, dst)
        return PutResult(
            version_id=None,
            etag=src_stat.etag,
            sha256=src_stat.sha256 or "",
            size=src_stat.size,
        )

    def _copy_blocking(self, src: ObjectLocation, dst: ObjectLocation) -> None:
        try:
            from minio.error import S3Error  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover
            S3Error = Exception  # type: ignore[assignment, misc]
        try:
            self._client.copy_object(
                dst.bucket,
                dst.object_key,
                _copy_source(src),
            )
        except S3Error as exc:  # type: ignore[misc]
            raise _map_s3_error(exc) from exc

    # -- Presign -----------------------------------------------------------

    async def presign_get(
        self,
        location: ObjectLocation,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        url = await asyncio.to_thread(
            self._presign_blocking, "GET", location, expires_in_seconds
        )
        return PresignedAccess(
            method="GET",
            url=url,
            expires_in_seconds=expires_in_seconds,
            location=location,
        )

    async def presign_put(
        self,
        location: ObjectLocation,
        expires_in_seconds: int = 900,
    ) -> PresignedAccess:
        url = await asyncio.to_thread(
            self._presign_blocking, "PUT", location, expires_in_seconds
        )
        return PresignedAccess(
            method="PUT",
            url=url,
            expires_in_seconds=expires_in_seconds,
            location=location,
        )

    def _presign_blocking(
        self,
        method: str,
        location: ObjectLocation,
        expires: int,
    ) -> str:
        try:
            from minio.error import S3Error  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover
            S3Error = Exception  # type: ignore[assignment, misc]
        try:
            if method == "GET":
                return self._client.presigned_get_object(
                    location.bucket, location.object_key, version_id=location.version_id
                )
            return self._client.presigned_put_object(location.bucket, location.object_key)
        except S3Error as exc:  # type: ignore[misc]
            raise _map_s3_error(exc) from exc

    # -- Multipart ---------------------------------------------------------

    async def initiate_multipart(
        self,
        location: ObjectLocation,
        options: PutOptions,
    ) -> UploadTicket:
        upload_id = await asyncio.to_thread(self._initiate_blocking, location)
        return UploadTicket(
            upload_id=upload_id,
            location=location,
            parts_expected=None,
            presigned_part_urls=(),
        )

    def _initiate_blocking(self, location: ObjectLocation) -> str:
        try:
            from minio.error import S3Error  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover
            S3Error = Exception  # type: ignore[assignment, misc]
        # The high-level minio SDK does not expose multipart directly; the
        # canonical path uses ``core.MultipartUploader``. We keep the seam and
        # raise a clear error until the production uploader is wired (M1 smoke).
        try:
            from minio.api import MultipartUploader  # type: ignore[import-not-found]
        except ImportError as exc:
            raise NotImplementedError(
                "minio multipart requires MultipartUploader (minio>=7.2); "
                "wire in M1 guarded smoke"
            ) from exc
        try:
            uploader = MultipartUploader(self._client, location.bucket, location.object_key)
            return uploader._create_upload_id()  # noqa: SLF001 - SDK internal hook
        except S3Error as exc:  # type: ignore[misc]
            raise _map_s3_error(exc) from exc

    async def upload_part(
        self,
        upload_id: str,
        part_number: int,
        stream: AsyncIterator[bytes],
    ) -> PartETag:
        raise NotImplementedError("minio multipart upload_part wired in M1 smoke")

    async def complete_multipart(
        self,
        upload_id: str,
        parts: tuple[PartETag, ...],
        expected_sha256: str | None = None,
    ) -> PutResult:
        raise NotImplementedError("minio multipart complete wired in M1 smoke")

    async def abort_multipart(self, upload_id: str) -> None:
        raise NotImplementedError("minio multipart abort wired in M1 smoke")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _FileReader:
    """Adapter exposing a file as the blocking binary stream minio expects."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._fh = open(path, "rb")  # noqa: SIM115 - closed in close()

    def read(self, n: int) -> bytes:
        return self._fh.read(n)

    def tell(self) -> int:
        return self._fh.tell()

    def seek(self, offset: int) -> int:
        return self._fh.seek(offset)

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass


def _meta_get(meta: Any, key: str) -> str | None:
    user_meta = getattr(meta, "metadata", None) or {}
    if isinstance(user_meta, dict) and key in user_meta:
        value = user_meta[key]
        return value if isinstance(value, str) else None
    return None


def _copy_source(src: ObjectLocation) -> Any:
    """Build a CopySource for the minio SDK (lazy import)."""
    from minio.commonconfig import CopySource  # type: ignore[import-not-found]

    return CopySource(src.bucket, src.object_key, version_id=src.version_id)


async def _drain(stream: AsyncIterator[bytes]) -> bytes:
    chunks: list[bytes] = []
    async for chunk in stream:
        chunks.append(chunk)
    return b"".join(chunks)


__all__ = ["MinioObjectStore"]
