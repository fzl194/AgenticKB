"""Service-level tests for ``FileMigrationService`` (M1.5, WP1C; ADR-0003 D-025).

All tests run hermetically against the in-memory fake repositories + the
filesystem ``FakeObjectStore`` + the filesystem ``FilesystemMigrationInventory``
— no PostgreSQL, no network, no MinIO. This is the primary correctness gate
for the legacy local-file migration (SRS §8.8 Phase 2, §A23).

Coverage:
- happy path: 3 files migrate -> all SWITCHED; document pointer + object written.
- idempotency: rerun the same inventory -> already-SWITCHED skipped (no
  duplicate upload / pointer advance).
- missing file: one path absent -> FAILED(missing_file); the rest continue;
  report.missing_files == 1.
- optimistic concurrency: a concurrent content_revision bump mid-migration ->
  FAILED(revision_conflict); pointer NOT switched.
- dedup: two documents with identical content reuse one StorageObject.
- dry_run: no writes, returns total estimate with missing-file sampling.
- resume: a run with one failure, then resume -> only the failed doc is retried.
- sha256 verify: the stat'd object sha256 equals the file's actual sha256.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

# On Windows the default ProactorEventLoop cannot drive the psycopg/async
# fakes the rest of the suite uses; switch to the selector policy for parity
# with the file_management tests.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryDocumentCurrentContentRepository,
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.file_migration.contracts import (  # noqa: E402
    REASON_MISSING_FILE,
    REASON_REVISION_CONFLICT,
    MigrationItem,
    MigrationTaskStatus,
)
from knowledge_mining.mining.file_migration.inventory_fs import (  # noqa: E402
    FilesystemMigrationInventory,
)
from knowledge_mining.mining.file_migration.progress_memory import (  # noqa: E402
    MemoryMigrationProgressStore,
)
from knowledge_mining.mining.file_migration.service import (  # noqa: E402
    FileMigrationService,
    FileMigrationServiceConfig,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _Handles:
    store: FakeObjectStore
    storage_objects: MemoryStorageObjectRepository
    documents: MemoryDocumentCurrentContentRepository
    progress: MemoryMigrationProgressStore
    service: FileMigrationService
    source_bucket: str


@pytest_asyncio.fixture
async def env(tmp_path):
    """Wire the service with empty fakes + a fresh FakeObjectStore root."""
    root = str(tmp_path / "objects")
    store = FakeObjectStore(root)
    storage_objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    progress = MemoryMigrationProgressStore()
    service = FileMigrationService(
        object_store=store,
        storage_objects=storage_objects,
        documents=documents,
        inventory=FilesystemMigrationInventory([]),  # set per-test
        progress=progress,
        config=FileMigrationServiceConfig(source_bucket="test-source"),
    )
    return _Handles(
        store=store,
        storage_objects=storage_objects,
        documents=documents,
        progress=progress,
        service=service,
        source_bucket="test-source",
    )


def _write_file(tmp_path: Path, name: str, content: bytes) -> Path:
    """Write a small legacy file under tmp_path and return its path."""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def _seed_document(documents: MemoryDocumentCurrentContentRepository, doc_id: str) -> int:
    """Pre-create a document row with a placeholder object so it exists.

    Returns the initial content_revision (1). The migration will then
    ``set_current_content`` against this revision. Must be awaited from a test.
    """
    await documents.create_document(
        kb_id="kb1",
        document_id=doc_id,
        folder_id=None,
        owner_id="actor1",
        document_name=f"{doc_id}.bin",
        document_type=None,
        storage_object_id="placeholder",
        source_raw_hash="placeholder",
    )
    doc = await documents.get(doc_id)
    return doc.content_revision if doc else 0


def _set_inventory(service: FileMigrationService, items: list[MigrationItem]) -> None:
    """Swap the service's inventory (mutate the in-place attribute for tests)."""
    service._inventory = FilesystemMigrationInventory(items)  # noqa: SLF001


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_three_files_all_switched(env: _Handles, tmp_path: Path) -> None:
    """Three legacy files migrate to SWITCHED; objects written, pointers set."""
    docs = [
        ("doc1", b"hello world content 1"),
        ("doc2", b"second document bytes"),
        ("doc3", b"third payload here"),
    ]
    items: list[MigrationItem] = []
    for doc_id, content in docs:
        path = _write_file(tmp_path, f"{doc_id}.bin", content)
        await _seed_document(env.documents, doc_id)
        items.append(
            MigrationItem(
                document_id=doc_id,
                kb_id="kb1",
                storage_path=str(path),
                current_content_revision=1,
                mime_hint="application/octet-stream",
            )
        )
    _set_inventory(env.service, items)

    report = await env.service.run()

    # Report shape.
    assert report.total == 3
    assert report.migrated == 3
    assert report.switched == 3
    assert report.failed == 0
    assert report.missing_files == 0
    assert report.duration_seconds >= 0.0
    assert len(report.per_document) == 3

    # Each document pointer advanced (revision 1 -> 2) and tied to a real object.
    for doc_id, content in docs:
        doc = await env.documents.get(doc_id)
        assert doc is not None
        assert doc.storage_object_id is not None
        assert doc.storage_object_id != "placeholder"
        assert doc.source_raw_hash == _sha256(content)
        assert doc.content_revision == 2  # incremented by set_current_content

        # The StorageObject exists and is AVAILABLE with the right hash.
        obj = await env.storage_objects.get(doc.storage_object_id)
        assert obj is not None
        assert obj.state == "AVAILABLE"
        assert obj.sha256 == _sha256(content)
        assert obj.size == len(content)
        assert obj.artifact_class == "source"

        # The object bytes are actually in the FakeObjectStore at the
        # content-addressed key (verify-before-switch invariant, SRS §A23).
        from knowledge_mining.mining.infra.object_store.keys import build_object_key

        key = build_object_key("source", obj.sha256)
        stat = await env.store.stat(
            __import__(
                "knowledge_mining.mining.contracts.storage.types",
                fromlist=["ObjectLocation"],
            ).ObjectLocation(bucket=env.source_bucket, object_key=key)
        )
        assert stat.sha256 == obj.sha256
        assert stat.size == len(content)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_rerun_skips_already_switched(env: _Handles, tmp_path: Path) -> None:
    """A second run over the same inventory does not re-write or re-advance."""
    path = _write_file(tmp_path, "doc1.bin", b"stable content")
    await _seed_document(env.documents, "doc1")
    items = [
        MigrationItem(
            document_id="doc1",
            kb_id="kb1",
            storage_path=str(path),
            current_content_revision=1,
        )
    ]
    _set_inventory(env.service, items)

    first = await env.service.run()
    assert first.switched == 1
    doc_after_first = await env.documents.get("doc1")
    assert doc_after_first is not None
    obj_id_after_first = doc_after_first.storage_object_id
    revision_after_first = doc_after_first.content_revision
    assert obj_id_after_first is not None

    # Second run: idempotent — no new object, no pointer advance.
    second = await env.service.run()
    assert second.switched == 1
    assert second.failed == 0
    doc_after_second = await env.documents.get("doc1")
    assert doc_after_second is not None
    assert doc_after_second.storage_object_id == obj_id_after_first
    assert doc_after_second.content_revision == revision_after_first

    # Only one StorageObject registered total.
    assert len(env.storage_objects._by_id) == 1  # noqa: SLF001


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


async def test_missing_file_failed_and_batch_continues(
    env: _Handles, tmp_path: Path
) -> None:
    """One absent path -> FAILED(missing_file); the remaining two still switch."""
    ok1 = _write_file(tmp_path, "ok1.bin", b"good one")
    ok2 = _write_file(tmp_path, "ok2.bin", b"good two")
    missing = tmp_path / "does_not_exist.bin"  # never written

    for doc_id in ("ok1", "ok2", "missing"):
        await _seed_document(env.documents, doc_id)

    items = [
        MigrationItem("ok1", "kb1", str(ok1), 1),
        MigrationItem("missing", "kb1", str(missing), 1),
        MigrationItem("ok2", "kb1", str(ok2), 1),
    ]
    _set_inventory(env.service, items)

    report = await env.service.run()

    assert report.total == 3
    assert report.switched == 2
    assert report.failed == 1
    assert report.missing_files == 1

    by_doc = {r.document_id: r for r in report.per_document}
    assert by_doc["missing"].status == MigrationTaskStatus.FAILED
    assert by_doc["missing"].error_reason == REASON_MISSING_FILE
    assert by_doc["ok1"].status == MigrationTaskStatus.SWITCHED
    assert by_doc["ok2"].status == MigrationTaskStatus.SWITCHED


# ---------------------------------------------------------------------------
# Optimistic concurrency conflict
# ---------------------------------------------------------------------------


async def test_optimistic_concurrency_conflict_does_not_switch(
    env: _Handles, tmp_path: Path
) -> None:
    """A concurrent content_revision bump mid-migration fails the task.

    The migration captured revision=1 at inventory time; we bump the document
    to revision 2 before calling migrate_document, so set_current_content
    raises DocumentRevisionConflict and the migration fails with
    revision_conflict — the new (concurrent) content wins (SRS §8.8).
    """
    path = _write_file(tmp_path, "doc.bin", b"original bytes")
    await _seed_document(env.documents, "doc")
    item = MigrationItem("doc", "kb1", str(path), current_content_revision=1)
    _set_inventory(env.service, [item])

    # Simulate a concurrent edit that advances the revision to 2.
    concurrent = await env.documents.set_current_content(
        "doc", "concurrent_obj", "concurrent_hash", expected_revision=1
    )
    assert concurrent.content_revision == 2

    result = await env.service.migrate_document(item)

    assert result.status == MigrationTaskStatus.FAILED
    assert result.error_reason == REASON_REVISION_CONFLICT

    # The pointer was NOT switched by the migration — it still reflects the
    # concurrent edit, not the migrated object.
    doc = await env.documents.get("doc")
    assert doc is not None
    assert doc.storage_object_id == "concurrent_obj"
    assert doc.source_raw_hash == "concurrent_hash"
    assert doc.content_revision == 2

    # The object WAS uploaded + registered (the failure is only at the switch).
    assert result.sha256 == _sha256(b"original bytes")
    assert len(env.storage_objects._by_id) == 1  # noqa: SLF001


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


async def test_dedup_two_documents_same_content_reuse_object(
    env: _Handles, tmp_path: Path
) -> None:
    """Two docs with identical bytes share one StorageObject (D-002 dedup)."""
    shared = b"identical payload for both docs"
    path_a = _write_file(tmp_path, "a.bin", shared)
    path_b = _write_file(tmp_path, "b.bin", shared)
    await _seed_document(env.documents, "docA")
    await _seed_document(env.documents, "docB")
    items = [
        MigrationItem("docA", "kb1", str(path_a), 1),
        MigrationItem("docB", "kb1", str(path_b), 1),
    ]
    _set_inventory(env.service, items)

    report = await env.service.run()
    assert report.switched == 2

    doc_a = await env.documents.get("docA")
    doc_b = await env.documents.get("docB")
    assert doc_a is not None and doc_b is not None
    # Both point to the SAME StorageObject id.
    assert doc_a.storage_object_id == doc_b.storage_object_id
    assert doc_a.source_raw_hash == doc_b.source_raw_hash == _sha256(shared)

    # Exactly one StorageObject + one object on disk.
    assert len(env.storage_objects._by_id) == 1  # noqa: SLF001


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


async def test_dry_run_no_writes_and_estimates_total(
    env: _Handles, tmp_path: Path
) -> None:
    """dry_run sizes the inventory + samples existence without writing."""
    present = _write_file(tmp_path, "present.bin", b"present")
    absent = tmp_path / "absent.bin"  # not written
    await _seed_document(env.documents, "present")
    await _seed_document(env.documents, "absent")
    items = [
        MigrationItem("present", "kb1", str(present), 1),
        MigrationItem("absent", "kb1", str(absent), 1),
    ]
    _set_inventory(env.service, items)

    report = await env.service.run(dry_run=True)

    assert report.total == 2
    assert report.migrated == 0
    assert report.switched == 0
    assert report.missing_files == 1  # sampled absent file

    # No objects written, no document pointers changed.
    assert len(env.storage_objects._by_id) == 0  # noqa: SLF001
    doc = await env.documents.get("present")
    assert doc is not None
    assert doc.storage_object_id == "placeholder"  # unchanged


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


async def test_resume_retries_only_failed(env: _Handles, tmp_path: Path) -> None:
    """A run with one missing file, then fix the file and resume -> it switches."""
    target = tmp_path / "recover.bin"
    # Initially the file is absent so the first run fails it.
    await _seed_document(env.documents, "recover")
    items = [MigrationItem("recover", "kb1", str(target), 1)]
    _set_inventory(env.service, items)

    first = await env.service.run()
    assert first.failed == 1
    assert first.missing_files == 1
    by_doc = {r.document_id: r for r in first.per_document}
    assert by_doc["recover"].status == MigrationTaskStatus.FAILED

    # Now materialize the file (simulate operator restoring from backup).
    target.write_bytes(b"recovered content")

    # Resume: re-iterates inventory, skips nothing (none SWITCHED), retries the
    # failed document now that the file exists.
    second = await env.service.resume()
    assert second.switched == 1
    assert second.failed == 0

    doc = await env.documents.get("recover")
    assert doc is not None
    assert doc.storage_object_id is not None
    assert doc.source_raw_hash == _sha256(b"recovered content")


async def test_resume_skips_switched_and_retries_failed(
    env: _Handles, tmp_path: Path
) -> None:
    """Mixed batch: resume skips the SWITCHED doc and only retries the FAILED one."""
    ok_path = _write_file(tmp_path, "ok.bin", b"ok content")
    bad_path = tmp_path / "bad.bin"  # absent initially
    await _seed_document(env.documents, "ok")
    await _seed_document(env.documents, "bad")
    items = [
        MigrationItem("ok", "kb1", str(ok_path), 1),
        MigrationItem("bad", "kb1", str(bad_path), 1),
    ]
    _set_inventory(env.service, items)

    first = await env.service.run()
    assert first.switched == 1
    assert first.failed == 1
    ok_obj_after_first = (await env.documents.get("ok")).storage_object_id  # type: ignore[union-attr]

    # Restore the missing file.
    bad_path.write_bytes(b"fixed content")

    second = await env.service.resume()
    assert second.switched == 2  # both now switched (ok re-reported as switched)
    assert second.failed == 0

    # The 'ok' doc was NOT re-touched (same object id).
    ok_obj_after_second = (await env.documents.get("ok")).storage_object_id  # type: ignore[union-attr]
    assert ok_obj_after_first == ok_obj_after_second
    # Only one object per unique content -> 2 objects total.
    assert len(env.storage_objects._by_id) == 2  # noqa: SLF001


# ---------------------------------------------------------------------------
# sha256 verification
# ---------------------------------------------------------------------------


async def test_uploaded_object_sha256_matches_file(env: _Handles, tmp_path: Path) -> None:
    """The stat'd object sha256 equals the file's actual sha256 (SRS §A23)."""
    content = b"verify me please \x00\x01\x02 binary"
    path = _write_file(tmp_path, "verify.bin", content)
    await _seed_document(env.documents, "verify")
    _set_inventory(
        env.service,
        [MigrationItem("verify", "kb1", str(path), 1)],
    )

    result = await env.service.migrate_document(
        MigrationItem("verify", "kb1", str(path), 1)
    )
    assert result.status == MigrationTaskStatus.SWITCHED
    assert result.sha256 == _sha256(content)

    # Cross-check via the store: stat the object at the content-addressed key.
    from knowledge_mining.mining.contracts.storage.types import ObjectLocation
    from knowledge_mining.mining.infra.object_store.keys import build_object_key

    key = build_object_key("source", result.sha256)
    stat = await env.store.stat(
        ObjectLocation(bucket=env.source_bucket, object_key=key)
    )
    assert stat.sha256 == _sha256(content)
    assert stat.size == len(content)


# ---------------------------------------------------------------------------
# Manifest-based inventory
# ---------------------------------------------------------------------------


async def test_inventory_from_manifest(tmp_path: Path) -> None:
    """FilesystemMigrationInventory.from_manifest loads a JSON list."""
    f = _write_file(tmp_path, "x.bin", b"manifest content")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "document_id": "docX",
                    "kb_id": "kb1",
                    "storage_path": str(f),
                    "current_content_revision": 1,
                    "mime_hint": "text/plain",
                }
            ]
        ),
        encoding="utf-8",
    )

    inv = FilesystemMigrationInventory.from_manifest(manifest)
    assert await inv.count_pending() == 1
    items = [item async for item in inv.iter_pending()]
    assert len(items) == 1
    assert items[0].document_id == "docX"
    assert items[0].mime_hint == "text/plain"


async def test_inventory_from_manifest_rejects_missing_keys(tmp_path: Path) -> None:
    """A manifest entry missing required keys raises ValueError."""
    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps([{"document_id": "x"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        FilesystemMigrationInventory.from_manifest(manifest)


# ---------------------------------------------------------------------------
# Report fields completeness
# ---------------------------------------------------------------------------


async def test_report_has_all_srs_fields(env: _Handles, tmp_path: Path) -> None:
    """MigrationReport exposes every field SRS §8.8 requires."""
    path = _write_file(tmp_path, "doc.bin", b"report fields")
    await _seed_document(env.documents, "doc")
    _set_inventory(env.service, [MigrationItem("doc", "kb1", str(path), 1)])

    report = await env.service.run()

    # SRS §8.8: total, migrated, missing, hash conflicts, permission failures,
    # orphan files, fallback reads.
    for field in (
        "total",
        "migrated",
        "switched",
        "failed",
        "missing_files",
        "hash_conflicts",
        "permission_failed",
        "orphan_files",
        "fallback_read_count",
        "duration_seconds",
        "per_document",
    ):
        assert hasattr(report, field), f"report missing field {field!r}"
    assert report.fallback_read_count == 0  # Phase 3 concern, not this tool
    assert isinstance(report.per_document, tuple)
