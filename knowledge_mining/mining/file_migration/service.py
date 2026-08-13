"""Legacy local-file migration service (M1.5, WP1C; ADR-0003 D-025).

``FileMigrationService`` is the hexagonal tool that drives SRS §8.8 Phase 2
(historical backfill). For each legacy ``asset_documents.storage_path`` row it:

  1. (idempotency) skips the document if the progress store already marks it
     ``SWITCHED``;
  2. streams the local file, computing sha256 incrementally;
  3. uploads the bytes to the source bucket at the content-addressed key, OR
     reuses an existing StorageObject at that location (D-002 dedup);
  4. verifies the stored object's ``size`` + ``sha256`` via ``stat`` (SRS §A23
     — verify-before-switch);
  5. registers / reuses a ``StorageObject`` (state AVAILABLE);
  6. advances the document's current-content pointer with optimistic
     concurrency on ``content_revision`` — a concurrent edit makes the task
     fail with ``revision_conflict`` and the new content wins (SRS §8.8).

It depends ONLY on the M1.1 ``ObjectStorePort``, the M1.2
``StorageObjectRepository`` / ``DocumentCurrentContentRepository`` Protocols,
and the local ``MigrationInventory`` / ``MigrationProgressStore`` Protocols.
No DB / psycopg / MinIO SDK imports live here; the PG and MinIO adapters (or
the in-memory fakes) are injected at construction. The service does NOT touch
DocumentService or jobs/run.py (ADR-0003 D-004).

Idempotency & recovery (SRS §8.8, §A23):
- ``migrate_document`` is a no-op for an already-SWITCHED document.
- ``run`` iterates the full inventory; ``resume`` retries only progress rows
  that are not yet SWITCHED.
- ``dry_run`` sizes the inventory and stats file existence without writing any
  object or DB row.

References:
- SRS §8.8 (Phase 0-6, esp. Phase 2 + report fields).
- SRS §8.7 (replacement boundary).
- SRS §A23 (acceptance).
- ADR-0003 D-002 (sha256 dedup), D-004 (M0 only adds columns), D-020 (location
  addressing), D-025 (this package's scope / no read-write path changes).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from knowledge_mining.mining.contracts.storage.types import PutResult

from knowledge_mining.mining.contracts.file_management import (
    DocumentCurrentContentRepository,
    DocumentRevisionConflict,
    StorageObjectRecord,
    StorageObjectRepository,
)
from knowledge_mining.mining.contracts.storage.errors import (
    ChecksumMismatch,
    StorageError,
    StorageObjectMissing,
)
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import (
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.file_migration.contracts import (
    REASON_HASH_CONFLICT,
    REASON_MISSING_FILE,
    REASON_ORPHAN,
    REASON_PERMISSION,
    REASON_REVISION_CONFLICT,
    REASON_UNKNOWN,
    MigrationInventory,
    MigrationItem,
    MigrationProgressStore,
    MigrationReport,
    MigrationTaskResult,
    MigrationTaskStatus,
)
from knowledge_mining.mining.infra.object_store.keys import build_object_key

# Streaming chunk size for local-file reads (64 KiB — matches FakeObjectStore).
_CHUNK_SIZE = 64 * 1024


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class FileMigrationServiceConfig:
    """Tuning knobs for the migration service."""

    bucket_prefix: str = "agentickb-dev-"
    source_bucket: str | None = None  # defaults to {prefix}source
    # Cap on number of documents processed per ``run`` (None = unlimited).
    # Used to bound a single batch; ``resume`` picks up the rest.
    default_limit: int | None = None
    # How many existence probes to sample in dry-run (None = all).
    dry_run_sample: int | None = None


class FileMigrationService:
    """Drives SRS §8.8 Phase 2 (historical backfill) per-document.

    Construct with the object store, the two repositories, an inventory, a
    progress store, and optional config. All public methods are async and
    idempotent per SRS §A23.
    """

    def __init__(
        self,
        *,
        object_store: ObjectStorePort,
        storage_objects: StorageObjectRepository,
        documents: DocumentCurrentContentRepository,
        inventory: MigrationInventory,
        progress: MigrationProgressStore,
        config: FileMigrationServiceConfig | None = None,
    ) -> None:
        self._store = object_store
        self._storage_objects = storage_objects
        self._documents = documents
        self._inventory = inventory
        self._progress = progress
        self._config = config or FileMigrationServiceConfig()

    # -- bucket resolution ------------------------------------------------

    def _source_bucket(self) -> str:
        return self._config.source_bucket or f"{self._config.bucket_prefix}source"

    def _provider_name(self) -> str:
        return getattr(self._store, "provider", "unknown")

    # -- single-document migration ---------------------------------------

    async def migrate_document(self, item: MigrationItem) -> MigrationTaskResult:
        """Migrate one document (SRS §8.8 Phase 2 + §A23 idempotent).

        Returns the terminal ``MigrationTaskResult`` (SWITCHED on success,
        FAILED with a reason otherwise). Each non-terminal step is recorded
        in the progress store so a crash leaves a resumable trail.
        """
        # 1. Idempotency guard (SRS §8.8: rerun is idempotent per document).
        existing = await self._progress.get(item.document_id)
        if existing is not None and existing.status == MigrationTaskStatus.SWITCHED:
            return existing
        await self._progress.upsert(
            MigrationTaskResult(
                document_id=item.document_id, status=MigrationTaskStatus.PENDING
            )
        )

        # 2. Open the legacy file (streaming). Missing/perm -> FAILED.
        opened = await self._open_or_fail(item)
        if isinstance(opened, MigrationTaskResult):
            return opened
        stream, size_read = opened

        # 3-5. Upload (streaming sha256) + dedup + verify + register.
        upload_outcome = await self._upload_and_register(item, stream, size_read)
        if isinstance(upload_outcome, _UploadFailure):
            return await self._fail(
                item, upload_outcome.reason, upload_outcome.bytes_migrated
            )
        storage_object = upload_outcome.object_
        sha256 = upload_outcome.sha256
        size = upload_outcome.size

        # 6. Verify-before-switch done in step 4; advance the pointer
        #    with optimistic concurrency (SRS §8.8).
        switch_outcome = await self._switch_pointer(item, storage_object)
        if isinstance(switch_outcome, _SwitchFailure):
            return await self._fail_switch(
                item, switch_outcome.reason, storage_object, sha256, size
            )

        return await self._finalize_switched(item, storage_object, sha256, size)

    async def _open_or_fail(
        self, item: MigrationItem
    ) -> "tuple[AsyncIterator[bytes], int] | MigrationTaskResult":
        """Open the legacy file or return a FAILED result for the caller.

        Centralizes the open + exception classification so :meth:`migrate_document`
        stays under the 50-line ceiling. Returns either the ``(stream, size)``
        pair or a terminal FAILED result.
        """
        try:
            return await self._open_legacy_file(item)
        except FileNotFoundError:
            return await self._fail(item, REASON_MISSING_FILE, 0)
        except PermissionError:
            return await self._fail(item, REASON_PERMISSION, 0)
        except OSError as exc:
            # Treat any other OS-level read failure as a missing-file variant
            # so the operator sees it in missing_files (SRS §8.8 missing).
            return await self._fail(item, REASON_MISSING_FILE, 0, detail=str(exc))

    async def _fail_switch(
        self,
        item: MigrationItem,
        reason: str,
        storage_object: StorageObjectRecord,
        sha256: str,
        size: int,
    ) -> MigrationTaskResult:
        """Record a FAILED-at-switch result carrying partial progress (§8.8 audit)."""
        return await self._fail(
            item,
            reason,
            size,
            storage_object_id=storage_object.id,
            sha256=sha256,
            size_value=size,
        )

    async def _finalize_switched(
        self,
        item: MigrationItem,
        storage_object: StorageObjectRecord,
        sha256: str,
        size: int,
    ) -> MigrationTaskResult:
        """Build + persist the terminal SWITCHED result."""
        result = MigrationTaskResult(
            document_id=item.document_id,
            status=MigrationTaskStatus.SWITCHED,
            storage_object_id=storage_object.id,
            sha256=sha256,
            size=size,
            error_reason=None,
            bytes_migrated=size,
        )
        await self._progress.upsert(result)
        return result

    async def _open_legacy_file(
        self, item: MigrationItem
    ) -> tuple[AsyncIterator[bytes], int]:
        """Open ``item.storage_path`` for streaming and return (stream, size).

        Raises ``FileNotFoundError`` / ``PermissionError`` / ``OSError`` to be
        classified by the caller. The size is read via ``os.stat`` (cheap).
        """
        path = item.storage_path
        stat = await asyncio.to_thread(os.stat, path)  # raises if missing/perm
        size = stat.st_size

        async def _stream() -> AsyncIterator[bytes]:
            fh = await asyncio.to_thread(open, path, "rb")
            try:
                while True:
                    chunk = await asyncio.to_thread(fh.read, _CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await asyncio.to_thread(fh.close)

        return _stream(), size

    async def _upload_and_register(
        self,
        item: MigrationItem,
        stream: AsyncIterator[bytes],
        size_hint: int,
    ) -> "_UploadOutcome":
        """Steps 3-5: streaming sha256 -> put (or dedup) -> stat verify.

        Returns the resolved ``StorageObjectRecord`` + sha256 + size on
        success, or a ``_UploadFailure`` carrying a ``REASON_*`` on failure.
        """
        # Mark UPLOADING.
        await self._progress.upsert(
            MigrationTaskResult(
                document_id=item.document_id,
                status=MigrationTaskStatus.UPLOADING,
            )
        )

        try:
            sha256, bytes_written = await self._hash_stream(stream)
        except OSError as exc:
            return _UploadFailure(REASON_MISSING_FILE, 0, detail=str(exc))

        bucket = self._source_bucket()
        final_key = build_object_key("source", sha256)

        # Dedup probe: a StorageObject at the final location means the bytes
        # are already in the store (D-002). Reuse it; do not re-upload.
        existing_obj = await self._storage_objects.find_by_location(
            bucket, final_key, None
        )
        if existing_obj is not None:
            return await self._verify_existing(item, existing_obj, sha256, bytes_written)

        # No existing record: upload the bytes to the final location.
        put_result = await self._put_bytes(bucket, final_key, sha256, bytes_written, item)
        if put_result is None:
            return _UploadFailure(REASON_UNKNOWN, bytes_written)

        # Verify the stored object (SRS §A23 verify-before-switch).
        stat_ok = await self._verify_stat(bucket, final_key, sha256, put_result.size)
        if stat_ok is not None:
            return _UploadFailure(stat_ok, bytes_written)

        record = await self._register_object(bucket, final_key, sha256, put_result.size, item)
        return _UploadSuccess(record, sha256, put_result.size)

    async def _hash_stream(
        self, stream: AsyncIterator[bytes]
    ) -> tuple[str, int]:
        """Drain ``stream`` while incrementally computing sha256 + size.

        Buffers the chunks in memory so the (small, test-sized) legacy files
        can be re-yielded to ``put_stream``. For very large legacy files the
        production variant would pipe directly through the store's streaming
        put and let the store compute the sha256; here we re-emit for clarity.
        """
        hasher = hashlib.sha256()
        total = 0
        async for chunk in stream:
            hasher.update(chunk)
            total += len(chunk)
        return hasher.hexdigest(), total

    async def _put_bytes(
        self,
        bucket: str,
        final_key: str,
        sha256: str,
        size: int,
        item: MigrationItem,
    ) -> PutResult | None:
        """Upload the (already-consumed) stream to the final location.

        Because ``_hash_stream`` drained the source stream, we re-read the
        legacy file here for the actual put. This keeps the hash and the put
        on independent code paths and lets ``put_stream`` fail-closed on a
        checksum mismatch via ``expected_sha256``.
        """
        path = item.storage_path

        async def _re_stream() -> AsyncIterator[bytes]:
            fh = await asyncio.to_thread(open, path, "rb")
            try:
                while True:
                    chunk = await asyncio.to_thread(fh.read, _CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await asyncio.to_thread(fh.close)

        location = ObjectLocation(bucket=bucket, object_key=final_key)
        options = PutOptions(
            artifact_class="source",
            mime=item.mime_hint,
            expected_sha256=sha256,
            content_length=size,
        )
        try:
            return await self._store.put_stream(location, _re_stream(), options)
        except ChecksumMismatch:
            return None
        except StorageError:
            return None

    async def _verify_existing(
        self,
        item: MigrationItem,
        existing: StorageObjectRecord,
        sha256: str,
        bytes_written: int,
    ) -> "_UploadOutcome":
        """Validate a dedup-hit StorageObject against the freshly computed hash."""
        if existing.sha256 != sha256:
            # Same location, different hash -> data integrity incident.
            return _UploadFailure(REASON_HASH_CONFLICT, bytes_written)
        return _UploadSuccess(existing, sha256, existing.size)

    async def _verify_stat(
        self,
        bucket: str,
        final_key: str,
        expected_sha256: str,
        expected_size: int,
    ) -> str | None:
        """HEAD/stat the just-written object; return a REASON_* or None on OK."""
        location = ObjectLocation(bucket=bucket, object_key=final_key)
        try:
            stat = await self._store.stat(location)
        except StorageObjectMissing:
            return REASON_ORPHAN
        except StorageError:
            return REASON_UNKNOWN
        if stat.sha256 != expected_sha256:
            return REASON_HASH_CONFLICT
        if stat.size != expected_size:
            return REASON_HASH_CONFLICT
        return None

    async def _register_object(
        self,
        bucket: str,
        final_key: str,
        sha256: str,
        size: int,
        item: MigrationItem,
    ) -> StorageObjectRecord:
        """Insert the StorageObject row (state AVAILABLE) and mark VERIFIED."""
        record = StorageObjectRecord(
            id=_new_id("obj"),
            provider=self._provider_name(),
            bucket=bucket,
            object_key=final_key,
            object_version_id=None,
            sha256=sha256,
            size=size,
            mime=item.mime_hint,
            artifact_class="source",
            state="AVAILABLE",
            etag=None,
            created_at=_utcnow(),
            last_verified_at=_utcnow(),
        )
        registered = await self._storage_objects.register(record)
        await self._progress.upsert(
            MigrationTaskResult(
                document_id=item.document_id,
                status=MigrationTaskStatus.VERIFIED,
                storage_object_id=registered.id,
                sha256=sha256,
                size=size,
                bytes_migrated=size,
            )
        )
        return registered

    async def _switch_pointer(
        self, item: MigrationItem, storage_object: StorageObjectRecord
    ) -> "_SwitchOutcome":
        """Step 6: advance the document current-content pointer (optimistic).

        A concurrent edit raises ``DocumentRevisionConflict``; the migration
        must NOT overwrite the new content (SRS §8.8) — it fails with
        ``revision_conflict`` instead.
        """
        try:
            await self._documents.set_current_content(
                item.document_id,
                storage_object.id,
                storage_object.sha256,
                expected_revision=item.current_content_revision,
            )
        except DocumentRevisionConflict:
            return _SwitchFailure(REASON_REVISION_CONFLICT)
        except KeyError:
            # Document row vanished mid-migration — treat as orphan data.
            return _SwitchFailure(REASON_ORPHAN)
        return _SwitchOK()

    async def _fail(
        self,
        item: MigrationItem,
        reason: str,
        bytes_migrated: int,
        *,
        detail: str | None = None,
        storage_object_id: str | None = None,
        sha256: str | None = None,
        size_value: int | None = None,
    ) -> MigrationTaskResult:
        """Record a FAILED result and return it (SRS §8.8 error reasons).

        The optional ``storage_object_id`` / ``sha256`` / ``size_value`` carry
        partial progress when a late step (e.g. the pointer switch) fails after
        the object was already uploaded + verified — useful for audit and for
        the operator deciding whether to reclaim the orphan object.
        """
        result = MigrationTaskResult(
            document_id=item.document_id,
            status=MigrationTaskStatus.FAILED,
            storage_object_id=storage_object_id,
            sha256=sha256,
            size=size_value,
            error_reason=reason,
            bytes_migrated=bytes_migrated,
        )
        await self._progress.upsert(result)
        _ = detail  # reserved for future logging; not stored on the result
        return result

    # -- batch drivers ----------------------------------------------------

    async def run(
        self,
        *,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> MigrationReport:
        """Run a migration batch over the inventory (SRS §8.8 Phase 2).

        ``dry_run`` sizes the inventory and stats file existence without
        writing any object or DB row — it produces an estimate report with
        ``missing_files`` populated from the sample.
        """
        start = _now_monotonic()
        if dry_run:
            return await self._dry_run(start)

        effective_limit = limit if limit is not None else self._config.default_limit
        results: list[MigrationTaskResult] = []
        count = 0
        async for item in self._inventory.iter_pending():
            if effective_limit is not None and count >= effective_limit:
                break
            results.append(await self.migrate_document(item))
            count += 1
        return _build_report(results, _elapsed(start))

    async def resume(self) -> MigrationReport:
        """Retry everything not yet SWITCHED (SRS §8.8 idempotent rerun).

        ``resume`` re-iterates the inventory and re-attempts every document
        whose progress is not yet ``SWITCHED``. The per-document idempotency
        guard inside :meth:`migrate_document` performs the actual skip, so a
        document that was SWITCHED between the crash and now is not re-touched
        (no duplicate upload, no duplicate pointer advance).

        The progress store is consulted up-front so a custom PG-backed
        ``resume`` implementation (which JOINs the asset_documents row to
        recover the path + revision) can short-circuit without iterating the
        whole inventory; the default implementation here re-iterates the
        inventory because the memory-backed progress result does not carry the
        original ``storage_path`` / ``current_content_revision``.
        """
        start = _now_monotonic()
        results: list[MigrationTaskResult] = []
        async for item in self._inventory.iter_pending():
            existing = await self._progress.get(item.document_id)
            if existing is not None and existing.status == MigrationTaskStatus.SWITCHED:
                # Already terminal-success — record and move on (SRS §8.8).
                results.append(existing)
                continue
            results.append(await self.migrate_document(item))
        return _build_report(results, _elapsed(start))

    async def _dry_run(self, start: float) -> MigrationReport:
        """Inventory sizing + file-existence sampling; no writes (SRS §8.8)."""
        total = await self._inventory.count_pending()
        missing = 0
        sample: list[MigrationTaskResult] = []
        seen = 0
        cap = self._config.dry_run_sample
        async for item in self._inventory.iter_pending():
            if cap is not None and seen >= cap:
                break
            seen += 1
            exists = await asyncio.to_thread(
                lambda p=item.storage_path: os.path.isfile(p)
            )
            if not exists:
                missing += 1
                sample.append(
                    MigrationTaskResult(
                        document_id=item.document_id,
                        status=MigrationTaskStatus.FAILED,
                        error_reason=REASON_MISSING_FILE,
                    )
                )
        return _build_estimate_report(
            total=total,
            sample_size=seen,
            sample_missing=missing,
            per_document=tuple(sample),
            duration=_elapsed(start),
        )


# ---------------------------------------------------------------------------
# Internal outcome types (sum types for the step results)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _UploadSuccess:
    object_: StorageObjectRecord
    sha256: str
    size: int


@dataclass(frozen=True)
class _UploadFailure:
    reason: str
    bytes_migrated: int
    detail: str | None = None


_UploadOutcome = _UploadSuccess | _UploadFailure


@dataclass(frozen=True)
class _SwitchOK:
    pass


@dataclass(frozen=True)
class _SwitchFailure:
    reason: str


_SwitchOutcome = _SwitchOK | _SwitchFailure


# ---------------------------------------------------------------------------
# Timing + report helpers (kept module-local; no extra util module)
# ---------------------------------------------------------------------------


def _now_monotonic() -> float:
    return datetime.now(timezone.utc).timestamp()


def _elapsed(start: float) -> float:
    return round(datetime.now(timezone.utc).timestamp() - start, 6)


def _build_report(results: list[MigrationTaskResult], duration: float) -> MigrationReport:
    """Aggregate per-document results into a MigrationReport (SRS §8.8 fields)."""
    migrated = sum(1 for r in results if r.status == MigrationTaskStatus.SWITCHED)
    failed = sum(1 for r in results if r.status == MigrationTaskStatus.FAILED)
    return MigrationReport(
        total=len(results),
        migrated=migrated,
        switched=migrated,
        failed=failed,
        missing_files=sum(1 for r in results if r.error_reason == REASON_MISSING_FILE),
        hash_conflicts=sum(1 for r in results if r.error_reason == REASON_HASH_CONFLICT),
        permission_failed=sum(1 for r in results if r.error_reason == REASON_PERMISSION),
        orphan_files=sum(1 for r in results if r.error_reason == REASON_ORPHAN),
        # fallback_read_count: local fallback reads are a Phase 3 concern; the
        # migration tool itself does not perform fallback reads, so this is 0.
        fallback_read_count=0,
        duration_seconds=duration,
        per_document=tuple(results),
    )


def _build_estimate_report(
    *,
    total: int,
    sample_size: int,
    sample_missing: int,
    per_document: tuple[MigrationTaskResult, ...],
    duration: float,
) -> MigrationReport:
    """Build a dry-run estimate report (no writes happened)."""
    return MigrationReport(
        total=total,
        migrated=0,
        switched=0,
        failed=0,
        missing_files=sample_missing,
        hash_conflicts=0,
        permission_failed=0,
        orphan_files=0,
        fallback_read_count=0,
        duration_seconds=duration,
        per_document=per_document,
    )


__all__ = [
    "FileMigrationService",
    "FileMigrationServiceConfig",
]
