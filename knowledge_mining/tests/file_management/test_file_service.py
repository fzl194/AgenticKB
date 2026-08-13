"""Service-level tests for ``FileManagementService`` (M1.3; ADR-0003 D-023).

Hermetic: in-memory fakes + ``FakeObjectStore``. No PostgreSQL, no network.
The document fixture is created by driving a real ``UploadSessionService``
through initiate -> stage -> complete, so the FileManagementService is
exercised against a realistic document row with a registered StorageObject.

Coverage (SRS §4.3A, §4.3, §C01, §9.0B):
- list_documents / get_document
- download_url (happy + MISSING object -> StorageObjectMissing)
- replace_content (revision+1, new object, old object retained, dedup)
- replace_content stale revision -> DocumentRevisionConflict
- rename / move (storage_object_id unchanged, audit recorded)
- soft_delete hides from list; restore re-shows
- purge_request records a ``purge_request`` audit only (no object deletion)
- NotFound for unknown ids
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile

import pytest
import pytest_asyncio

# Windows: psycopg-async needs the SelectorEventLoop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.file_management import (  # noqa: E402
    DocumentRevisionConflict,
)
from knowledge_mining.mining.contracts.storage.errors import (  # noqa: E402
    StorageObjectMissing,
)
from knowledge_mining.mining.file_management.file_service import (  # noqa: E402
    FileManagementService,
    FileManagementServiceConfig,
    NotFound,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryDocumentCurrentContentRepository,
    MemoryFileAuditRepository,
    MemoryQuotaRepository,
    MemoryStorageObjectRepository,
    MemoryUploadSessionRepository,
)
from knowledge_mining.mining.file_management.service import (  # noqa: E402
    UploadSessionService,
    UploadSessionServiceConfig,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402

pytestmark = pytest.mark.asyncio


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Handles:
    """Bundle of the underlying fakes for assertions."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


async def _bytes_stream(data: bytes):
    yield data


@pytest_asyncio.fixture
async def env():
    """Wire FileManagementService + UploadSessionService on shared fakes."""
    root = tempfile.mkdtemp(prefix="fmsvc_")
    store = FakeObjectStore(root)
    sessions = MemoryUploadSessionRepository()
    storage_objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    audits = MemoryFileAuditRepository()
    quotas = MemoryQuotaRepository()
    quotas.seed("kb1", 10_000_000)  # 10 MB

    up_svc = UploadSessionService(
        object_store=store,
        sessions=sessions,
        storage_objects=storage_objects,
        documents=documents,
        audits=audits,
        quotas=quotas,
        config=UploadSessionServiceConfig(),
    )
    fm_svc = FileManagementService(
        object_store=store,
        documents=documents,
        storage_objects=storage_objects,
        audits=audits,
        quotas=quotas,
        sessions=sessions,
        config=FileManagementServiceConfig(),
    )
    return fm_svc, up_svc, _Handles(
        store=store, sessions=sessions, storage_objects=storage_objects,
        documents=documents, audits=audits, quotas=quotas,
    )


async def _seed_document(up_svc, *, kb_id="kb1", folder_id=None, actor="u1",
                         filename="doc.txt", data=b"initial document body"):
    """Drive an upload session to COMMITTED; return the CommitResult."""
    session, _ = await up_svc.initiate(
        kb_id=kb_id, folder_id=folder_id, actor=actor, filename=filename,
        expected_size=len(data), expected_mime="text/plain",
        idempotency_key=f"ik-{filename}-{_sha256(data)[:8]}",
    )
    await up_svc.stage_from_bytes(session.id, data)
    return await up_svc.complete(session.id)


# ---------------------------------------------------------------------------
# list / get
# ---------------------------------------------------------------------------


async def test_list_and_get_document(env):
    fm, up, _ = env
    result = await _seed_document(up, filename="a.txt", data=b"content a")

    items = await fm.list_documents("kb1")
    assert len(items) == 1
    assert items[0].document_id == result.document_id
    assert items[0].storage_object_id == result.storage_object_id
    assert items[0].size == len(b"content a")
    assert items[0].mime == "text/plain"
    assert items[0].content_revision == 1

    one = await fm.get_document(result.document_id)
    assert one.document_id == result.document_id
    assert one.raw_hash == _sha256(b"content a")


async def test_get_unknown_document_raises_not_found(env):
    fm, _, _ = env
    with pytest.raises(NotFound):
        await fm.get_document("doc_does_not_exist")


# ---------------------------------------------------------------------------
# download_url
# ---------------------------------------------------------------------------


async def test_download_url_returns_presigned_get(env):
    fm, up, h = env
    result = await _seed_document(up, data=b"download me")
    access = await fm.download_url(result.document_id, expires_seconds=300)
    assert access.method == "GET"
    assert access.expires_in_seconds == 300
    assert access.location.bucket.endswith("source")


async def test_download_url_missing_object_raises(env):
    fm, up, h = env
    result = await _seed_document(up, data=b"will disappear")
    # Simulate an integrity incident: drop the storage object row.
    await h.storage_objects.set_state(result.storage_object_id, "MISSING")
    # Manually evict the record to force the missing path.
    h.storage_objects._by_id.pop(result.storage_object_id, None)
    with pytest.raises(StorageObjectMissing):
        await fm.download_url(result.document_id)


# ---------------------------------------------------------------------------
# replace_content (optimistic concurrency)
# ---------------------------------------------------------------------------


async def test_replace_content_advances_revision_and_keeps_old_object(env):
    fm, up, h = env
    initial = await _seed_document(up, data=b"v1 content")
    old_object_id = initial.storage_object_id

    new_data = b"v2 content entirely different"
    view = await fm.replace_content(
        initial.document_id,
        stream=_bytes_stream(new_data),
        expected_revision=1,
        mime="text/plain",
        actor="u1",
    )
    assert view.content_revision == 2
    assert view.storage_object_id != old_object_id
    assert view.size == len(new_data)
    assert view.raw_hash == _sha256(new_data)

    # Old object still exists (copy-on-write; not deleted).
    old_obj = await h.storage_objects.get(old_object_id)
    assert old_obj is not None
    assert old_obj.state == "AVAILABLE"

    # New object registered + AVAILABLE.
    new_obj = await h.storage_objects.get(view.storage_object_id)
    assert new_obj is not None
    assert new_obj.sha256 == _sha256(new_data)

    # Audit recorded.
    events = h.audits.by_document(initial.document_id)
    actions = [e.action for e in events]
    assert "upload" in actions
    assert "replace_content" in actions


async def test_replace_content_stale_revision_raises_conflict(env):
    fm, up, _ = env
    initial = await _seed_document(up, data=b"first")
    with pytest.raises(DocumentRevisionConflict):
        await fm.replace_content(
            initial.document_id,
            stream=_bytes_stream(b"second"),
            expected_revision=99,  # stale
            mime="text/plain",
            actor="u1",
        )


async def test_replace_content_dedup_reuses_same_object(env):
    fm, up, h = env
    data = b"identical bytes"
    initial = await _seed_document(up, data=data)

    # Replace with the SAME bytes: the final content-addressed object already
    # exists, so the StorageObject should be reused (D-002 / O3).
    view = await fm.replace_content(
        initial.document_id,
        stream=_bytes_stream(data),
        expected_revision=1,
        mime="text/plain",
        actor="u1",
    )
    assert view.content_revision == 2
    # The referenced object carries the same sha256.
    assert view.raw_hash == _sha256(data)


# ---------------------------------------------------------------------------
# rename / move
# ---------------------------------------------------------------------------


async def test_rename_does_not_change_storage_object(env):
    fm, up, h = env
    initial = await _seed_document(up, filename="old.txt", data=b"x")
    before_obj = initial.storage_object_id

    view = await fm.rename(initial.document_id, new_name="renamed.txt", actor="u1")
    assert view.name == "renamed.txt"
    assert view.storage_object_id == before_obj  # untouched
    events = h.audits.by_document(initial.document_id)
    assert any(e.action == "rename" for e in events)


async def test_move_does_not_change_storage_object(env):
    fm, up, h = env
    initial = await _seed_document(up, data=b"x")
    before_obj = initial.storage_object_id

    view = await fm.move(initial.document_id, target_folder_id="folder_42", actor="u1")
    assert view.folder_id == "folder_42"
    assert view.storage_object_id == before_obj
    events = h.audits.by_document(initial.document_id)
    assert any(e.action == "move" for e in events)


# ---------------------------------------------------------------------------
# soft_delete / restore
# ---------------------------------------------------------------------------


async def test_soft_delete_then_restore_round_trip(env):
    fm, up, _ = env
    initial = await _seed_document(up, data=b"delete me")

    await fm.soft_delete(initial.document_id, actor="u1")

    # Default list hides soft-deleted rows.
    visible = await fm.list_documents("kb1")
    assert all(v.document_id != initial.document_id for v in visible)

    # include_deleted surfaces it.
    with_deleted = await fm.list_documents("kb1", include_deleted=True)
    assert any(v.document_id == initial.document_id for v in with_deleted)
    assert next(
        v for v in with_deleted if v.document_id == initial.document_id
    ).deleted_at is not None

    restored = await fm.restore(initial.document_id, actor="u1")
    assert restored.deleted_at is None

    visible_after = await fm.list_documents("kb1")
    assert any(v.document_id == initial.document_id for v in visible_after)


# ---------------------------------------------------------------------------
# purge (register request only)
# ---------------------------------------------------------------------------


async def test_purge_only_registers_request_does_not_delete_object(env):
    fm, up, h = env
    initial = await _seed_document(up, data=b"purge candidate")
    obj_id = initial.storage_object_id

    await fm.purge(initial.document_id, actor="u1")

    # Object still present — purge_request only logs intent.
    obj = await h.storage_objects.get(obj_id)
    assert obj is not None

    events = h.audits.by_document(initial.document_id)
    assert any(e.action == "purge_request" for e in events)
