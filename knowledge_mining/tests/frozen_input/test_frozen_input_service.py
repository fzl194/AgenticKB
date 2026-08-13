"""Tests for ``FrozenInputService`` (M1.4, WP1D — freeze + check_stale).

All tests run against the in-memory fake repositories + the filesystem
``FakeObjectStore`` — no PostgreSQL, no MinIO. Coverage (SRS §3.2, §C02,
§9.5):

- ``freeze`` snapshots the correct ``(storage_object_id, raw_hash,
  content_revision)`` triple plus resolved location.
- ``freeze`` rejects when the document is missing, the storage object is
  missing, or the storage object is not in the AVAILABLE state.
- ``check_stale`` passes when the live revision matches the frozen one.
- ``check_stale`` raises ``FrozenInputStale`` when the document was edited
  (revision bumped) or deleted between freeze and commit.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile

import pytest
import pytest_asyncio

# psycopg-async needs the SelectorEventLoop on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.file_management import (  # noqa: E402
    StorageObjectRecord,
)
from knowledge_mining.mining.contracts.storage.errors import (  # noqa: E402
    StorageObjectMissing,
)
from knowledge_mining.mining.contracts.storage.types import (  # noqa: E402
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryDocumentCurrentContentRepository,
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.frozen_input.contracts import FrozenInputStale  # noqa: E402
from knowledge_mining.mining.frozen_input.service import FrozenInputService  # noqa: E402
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _storage_object_record(
    *,
    storage_object_id: str,
    sha: str,
    size: int,
    bucket: str,
    object_key: str,
    state: str = "AVAILABLE",
    mime: str = "text/plain",
) -> StorageObjectRecord:
    return StorageObjectRecord(
        id=storage_object_id,
        provider="fake",
        bucket=bucket,
        object_key=object_key,
        object_version_id=None,
        sha256=sha,
        size=size,
        mime=mime,
        artifact_class="source",
        state=state,
        etag=sha[:32],
    )


class _Env:
    """Bundle of wired-up dependencies for post-action assertions."""

    def __init__(
        self,
        *,
        store: FakeObjectStore,
        documents: MemoryDocumentCurrentContentRepository,
        storage_objects: MemoryStorageObjectRepository,
        service: FrozenInputService,
    ) -> None:
        self.store = store
        self.documents = documents
        self.storage_objects = storage_objects
        self.service = service


async def _seed_document_with_object(
    *,
    documents: MemoryDocumentCurrentContentRepository,
    storage_objects: MemoryStorageObjectRepository,
    store: FakeObjectStore,
    document_id: str,
    data: bytes,
    storage_object_id: str,
    bucket: str = "kb1-source",
    object_key: str | None = None,
    state: str = "AVAILABLE",
) -> StorageObjectRecord:
    """Seed a document + an AVAILABLE storage object + the object bytes."""
    sha = _sha256(data)
    key = object_key or f"v1/ab/cd/{sha}"
    location = ObjectLocation(bucket=bucket, object_key=key)
    await store.put_bytes(
        location,
        data,
        PutOptions(artifact_class="source", mime="text/plain"),
    )
    record = _storage_object_record(
        storage_object_id=storage_object_id,
        sha=sha,
        size=len(data),
        bucket=bucket,
        object_key=key,
        state=state,
    )
    await storage_objects.register(record)
    await documents.create_document(
        kb_id="kb1",
        document_id=document_id,
        folder_id=None,
        owner_id="u1",
        document_name=document_id,
        document_type="text",
        storage_object_id=storage_object_id,
        source_raw_hash=sha,
    )
    return record


@pytest_asyncio.fixture
async def env() -> _Env:
    root = tempfile.mkdtemp(prefix="frozen_test_")
    store = FakeObjectStore(root)
    documents = MemoryDocumentCurrentContentRepository()
    storage_objects = MemoryStorageObjectRepository()
    service = FrozenInputService(
        documents=documents,
        storage_objects=storage_objects,
        object_store=store,
    )
    return _Env(
        store=store,
        documents=documents,
        storage_objects=storage_objects,
        service=service,
    )


# ---------------------------------------------------------------------------
# freeze — happy path
# ---------------------------------------------------------------------------


async def test_freeze_captures_binding(env: _Env) -> None:
    data = b"hello frozen world"
    await _seed_document_with_object(
        documents=env.documents,
        storage_objects=env.storage_objects,
        store=env.store,
        document_id="doc1",
        data=data,
        storage_object_id="so_1",
    )

    frozen = await env.service.freeze("doc1")

    assert frozen.document_id == "doc1"
    assert frozen.source_storage_object_id == "so_1"
    assert frozen.source_raw_hash == _sha256(data)
    assert frozen.source_content_revision == 1
    assert frozen.size == len(data)
    assert frozen.mime == "text/plain"
    assert frozen.captured_at  # non-empty ISO timestamp
    # Location snapshot for the reader.
    assert frozen.provider == "fake"
    assert frozen.bucket == "kb1-source"
    assert frozen.object_key.startswith("v1/ab/cd/")
    assert frozen.object_version_id is None


# ---------------------------------------------------------------------------
# freeze — error paths
# ---------------------------------------------------------------------------


async def test_freeze_missing_document_raises(env: _Env) -> None:
    with pytest.raises(StorageObjectMissing):
        await env.service.freeze("no_such_doc")


async def test_freeze_missing_storage_object_raises(env: _Env) -> None:
    # Create a document row but never register the storage object it points at.
    await env.documents.create_document(
        kb_id="kb1",
        document_id="doc_orphan",
        folder_id=None,
        owner_id="u1",
        document_name="doc_orphan",
        document_type="text",
        storage_object_id="so_ghost",
        source_raw_hash=_sha256(b"x"),
    )
    with pytest.raises(StorageObjectMissing):
        await env.service.freeze("doc_orphan")


async def test_freeze_non_available_storage_object_raises(env: _Env) -> None:
    data = b"still staging"
    await _seed_document_with_object(
        documents=env.documents,
        storage_objects=env.storage_objects,
        store=env.store,
        document_id="doc_staging",
        data=data,
        storage_object_id="so_staging",
        state="STAGING",  # not yet AVAILABLE
    )
    with pytest.raises(StorageObjectMissing) as exc_info:
        await env.service.freeze("doc_staging")
    msg = str(exc_info.value)
    assert "STAGING" in msg


# ---------------------------------------------------------------------------
# check_stale
# ---------------------------------------------------------------------------


async def test_check_stale_passes_when_revision_unchanged(env: _Env) -> None:
    data = b"stable content"
    await _seed_document_with_object(
        documents=env.documents,
        storage_objects=env.storage_objects,
        store=env.store,
        document_id="doc_stable",
        data=data,
        storage_object_id="so_stable",
    )
    frozen = await env.service.freeze("doc_stable")

    # No edit in between — should not raise.
    await env.service.check_stale(frozen)


async def test_check_stale_raises_when_revision_advanced(env: _Env) -> None:
    data_v1 = b"version one"
    await _seed_document_with_object(
        documents=env.documents,
        storage_objects=env.storage_objects,
        store=env.store,
        document_id="doc_edit",
        data=data_v1,
        storage_object_id="so_v1",
    )
    frozen = await env.service.freeze("doc_edit")

    # Simulate the user editing the document: current content moves to a new
    # storage object, content_revision bumps to 2.
    data_v2 = b"version two"
    sha2 = _sha256(data_v2)
    await env.store.put_bytes(
        ObjectLocation(bucket="kb1-source", object_key="v1/ab/cd/" + sha2),
        data_v2,
        PutOptions(artifact_class="source", mime="text/plain"),
    )
    await env.storage_objects.register(
        _storage_object_record(
            storage_object_id="so_v2",
            sha=sha2,
            size=len(data_v2),
            bucket="kb1-source",
            object_key="v1/ab/cd/" + sha2,
        )
    )
    await env.documents.set_current_content(
        document_id="doc_edit",
        storage_object_id="so_v2",
        raw_hash=sha2,
        expected_revision=1,
    )

    # The frozen binding still references revision 1 — staleness is detected.
    with pytest.raises(FrozenInputStale) as exc_info:
        await env.service.check_stale(frozen)
    assert exc_info.value.frozen_revision == 1
    assert exc_info.value.current_revision == 2


async def test_check_stale_raises_when_document_deleted(env: _Env) -> None:
    data = b"doomed"
    await _seed_document_with_object(
        documents=env.documents,
        storage_objects=env.storage_objects,
        store=env.store,
        document_id="doc_doom",
        data=data,
        storage_object_id="so_doom",
    )
    frozen = await env.service.freeze("doc_doom")

    # Simulate hard-delete by dropping the row from the in-memory store.
    env.documents._docs.pop("doc_doom")  # noqa: SLF001 - test-only reach into fake

    with pytest.raises(FrozenInputStale) as exc_info:
        await env.service.check_stale(frozen)
    assert exc_info.value.current_revision == -1  # sentinel for "disappeared"
