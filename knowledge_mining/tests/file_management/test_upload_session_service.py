"""Service-level tests for ``UploadSessionService`` (M1.2, WP1B).

All tests run hermetically against the in-memory fake repositories + the
filesystem ``FakeObjectStore`` — no PostgreSQL, no network, no MinIO. This is
the primary correctness gate for the upload-session lifecycle.

Coverage (SRS §4.1A, §4.3A, §9.0A, §9.5, §C01):
- happy path: initiate -> stage -> complete -> AVAILABLE object + revision 1.
- idempotency: repeated initiate returns the same session (no double-reserve);
  repeated complete returns the same CommitResult (no double-write).
- checksum verification (expected_sha256 mismatch -> ChecksumMismatch).
- size mismatch -> UploadIncomplete.
- abort: releases quota, deletes staging, blocks subsequent complete.
- quota: reserve on initiate, commit on complete, release on abort;
  over-limit initiate -> QuotaExceeded.
- optimistic concurrency: stale expected_revision -> DocumentRevisionConflict.
- dedup: same sha256 second upload reuses the StorageObject (no second copy).
- state machine: illegal transitions rejected.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile

import pytest
import pytest_asyncio

# psycopg-async (imported transitively via FakeObjectStore is fine, but the
# service is fully async) needs the SelectorEventLoop on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.file_management import (  # noqa: E402
    CommitResult,
    DocumentRevisionConflict,
    QuotaExceeded,
    UploadIncomplete,
    UploadSessionExpired,
)
from knowledge_mining.mining.contracts.state_machines import IllegalTransition  # noqa: E402
from knowledge_mining.mining.contracts.storage.errors import ChecksumMismatch  # noqa: E402
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

# Apply the asyncio marker to every async test in this module (strict mode).
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest_asyncio.fixture
async def env():
    """A fully wired service with in-memory fakes + a FakeObjectStore."""
    root = tempfile.mkdtemp(prefix="fm_test_")
    store = FakeObjectStore(root)
    sessions = MemoryUploadSessionRepository()
    storage_objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    audits = MemoryFileAuditRepository()
    quotas = MemoryQuotaRepository()
    quotas.seed("kb1", 1_000_000)  # 1 MB limit

    svc = UploadSessionService(
        object_store=store,
        sessions=sessions,
        storage_objects=storage_objects,
        documents=documents,
        audits=audits,
        quotas=quotas,
    )
    return svc, _ServiceHandles(
        store=store, sessions=sessions, storage_objects=storage_objects,
        documents=documents, audits=audits, quotas=quotas,
    )


class _ServiceHandles:
    """Bundle of the underlying fakes for post-action assertions."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_creates_object_and_document(env):
    svc, h = env
    data = b"hello world document body"

    session, presign = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data), expected_mime="text/plain", idempotency_key="ik1",
    )
    assert session.state == "INITIATED"
    assert presign.method == "PUT"

    staged = await svc.stage_from_bytes(session.id, data)
    assert staged.state == "OBJECT_STAGED"

    result = await svc.complete(session.id)
    assert isinstance(result, CommitResult)
    assert result.size == len(data)
    assert result.sha256 == _sha256(data)
    assert result.content_revision == 1

    # StorageObject registered and AVAILABLE.
    obj = await h.storage_objects.get(result.storage_object_id)
    assert obj is not None
    assert obj.state == "AVAILABLE"
    assert obj.sha256 == _sha256(data)
    assert obj.bucket.endswith("source")  # final bucket, not staging

    # Document current content points at the new object, revision 1.
    doc = await h.documents.get(result.document_id)
    assert doc is not None
    assert doc.storage_object_id == result.storage_object_id
    assert doc.content_revision == 1
    assert doc.source_raw_hash == _sha256(data)

    # Audit event recorded.
    events = h.audits.by_document(result.document_id)
    assert len(events) == 1
    assert events[0].action == "upload"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_initiate_idempotent_returns_same_session(env):
    svc, h = env
    data = b"same bytes"
    s1, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data), expected_mime="text/plain", idempotency_key="ik-dup",
    )
    s2, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data), expected_mime="text/plain", idempotency_key="ik-dup",
    )
    assert s1.id == s2.id
    # Quota reserved exactly once (version advanced by 1, not 2).
    quota = await h.quotas.get("kb1")
    assert quota.reserved_bytes == len(data)


async def test_complete_idempotent_returns_same_result(env):
    svc, h = env
    data = b"idempotent complete payload"
    session, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data), expected_mime="text/plain", idempotency_key="ik-c",
    )
    await svc.stage_from_bytes(session.id, data)
    r1 = await svc.complete(session.id)
    r2 = await svc.complete(session.id)
    assert r1 == r2
    # Only one StorageObject registered.
    assert len(h.storage_objects._by_id) == 1  # noqa: SLF001
    # Audit: only one upload event (re-complete does not re-audit).
    events = h.audits.by_document(r1.document_id)
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Checksum + size verification
# ---------------------------------------------------------------------------


async def test_complete_checksum_mismatch_raises(env):
    svc, h = env
    data = b"some bytes to verify"
    session, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data), expected_mime="text/plain", idempotency_key="ik-ck",
    )
    await svc.stage_from_bytes(session.id, data)
    wrong = "0" * 64
    with pytest.raises(ChecksumMismatch):
        await svc.complete(session.id, expected_sha256=wrong)
    # State did not advance to COMMITTED.
    current = await h.sessions.get(session.id)
    assert current.state != "COMMITTED"


async def test_complete_size_mismatch_raises_upload_incomplete(env):
    svc, h = env
    data = b"declared 10 bytes but these are more"
    session, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=10, expected_mime="text/plain", idempotency_key="ik-sz",
    )
    await svc.stage_from_bytes(session.id, data)
    with pytest.raises(UploadIncomplete):
        await svc.complete(session.id)


async def test_complete_with_correct_checksum_succeeds(env):
    svc, _ = env
    data = b"verify me"
    session, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data), expected_mime="text/plain", idempotency_key="ik-ok",
    )
    await svc.stage_from_bytes(session.id, data)
    result = await svc.complete(session.id, expected_sha256=_sha256(data))
    assert result.sha256 == _sha256(data)


# ---------------------------------------------------------------------------
# Abort
# ---------------------------------------------------------------------------


async def test_abort_releases_quota_and_blocks_complete(env):
    svc, h = env
    data = b"to be aborted"
    session, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data), expected_mime="text/plain", idempotency_key="ik-ab",
    )
    quota_after_init = await h.quotas.get("kb1")
    assert quota_after_init.reserved_bytes == len(data)

    aborted = await svc.abort(session.id)
    assert aborted.state == "ABORTED"

    quota_after_abort = await h.quotas.get("kb1")
    assert quota_after_abort.reserved_bytes == 0

    # Subsequent complete must fail (session terminal).
    with pytest.raises(UploadSessionExpired):
        await svc.complete(session.id)


# ---------------------------------------------------------------------------
# Quota enforcement
# ---------------------------------------------------------------------------


async def test_initiate_over_limit_raises_quota_exceeded(env):
    svc, h = env
    h.quotas.seed("kb-tiny", 8)  # 8-byte limit
    with pytest.raises(QuotaExceeded):
        await svc.initiate(
            kb_id="kb-tiny", folder_id=None, actor="u1", filename="big.bin",
            expected_size=100, expected_mime="application/octet-stream",
            idempotency_key="ik-big",
        )


async def test_quota_commit_on_complete_and_release_on_abort(env):
    svc, h = env
    data = b"quota tracking bytes"
    session, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data), expected_mime="text/plain", idempotency_key="ik-q1",
    )
    assert (await h.quotas.get("kb1")).reserved_bytes == len(data)
    await svc.stage_from_bytes(session.id, data)
    await svc.complete(session.id)
    quota = await h.quotas.get("kb1")
    assert quota.reserved_bytes == 0
    assert quota.used_bytes == len(data)

    # Abort path on a separate session releases reserved.
    session2, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="g.txt",
        expected_size=50, expected_mime="text/plain", idempotency_key="ik-q2",
    )
    assert (await h.quotas.get("kb1")).reserved_bytes == 50
    await svc.abort(session2.id)
    assert (await h.quotas.get("kb1")).reserved_bytes == 0
    # used_bytes unchanged by abort.
    assert (await h.quotas.get("kb1")).used_bytes == len(data)


# ---------------------------------------------------------------------------
# Optimistic concurrency on document content
# ---------------------------------------------------------------------------


async def test_replace_content_with_stale_revision_raises_conflict(env):
    svc, h = env
    data_v1 = b"first version"
    session, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data_v1), expected_mime="text/plain", idempotency_key="ik-v1",
    )
    await svc.stage_from_bytes(session.id, data_v1)
    r1 = await svc.complete(session.id)
    assert r1.content_revision == 1

    # Second upload, same logical document, fresh revision -> 2.
    data_v2 = b"second version is longer"
    session2, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data_v2), expected_mime="text/plain", idempotency_key="ik-v2",
    )
    await svc.stage_from_bytes(session2.id, data_v2)
    r2 = await svc.complete(session2.id, document_id=r1.document_id)
    assert r2.content_revision == 2

    # Simulate a concurrent writer holding the stale revision: call the repo
    # directly with the old revision and expect the conflict.
    obj_v2 = await h.storage_objects.get(r2.storage_object_id)
    with pytest.raises(DocumentRevisionConflict):
        await h.documents.set_current_content(
            r1.document_id,
            obj_v2.id,
            obj_v2.sha256,
            expected_revision=1,  # stale; current is 2
        )


# ---------------------------------------------------------------------------
# Dedup (same sha256 reuses StorageObject)
# ---------------------------------------------------------------------------


async def test_dedup_same_sha256_reuses_storage_object(env):
    svc, h = env
    data = b"duplicate me"

    async def _upload(idem_key: str) -> CommitResult:
        s, _ = await svc.initiate(
            kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
            expected_size=len(data), expected_mime="text/plain", idempotency_key=idem_key,
        )
        await svc.stage_from_bytes(s.id, data)
        return await svc.complete(s.id)

    r1 = await _upload("ik-d1")
    r2 = await _upload("ik-d2")
    assert r1.storage_object_id == r2.storage_object_id
    # Two different logical documents, but one StorageObject.
    assert len(h.storage_objects._by_id) == 1  # noqa: SLF001
    # Final object exists; second upload did not copy again (single object at the
    # content-addressed key).
    final_key = r1.storage_object_id
    obj = await h.storage_objects.get(final_key)
    assert obj is not None
    assert obj.sha256 == _sha256(data)


# ---------------------------------------------------------------------------
# State machine enforcement
# ---------------------------------------------------------------------------


async def test_illegal_transition_rejected_on_abort_after_commit(env):
    svc, _ = env
    data = b"committed then abort"
    session, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=len(data), expected_mime="text/plain", idempotency_key="ik-sm",
    )
    await svc.stage_from_bytes(session.id, data)
    await svc.complete(session.id)
    # Aborting a COMMITTED session raises UploadSessionExpired (guarded before
    # the transition); the illegal COMMITTED->ABORTED transition never fires.
    with pytest.raises(UploadSessionExpired):
        await svc.abort(session.id)


async def test_complete_without_staging_raises_upload_incomplete(env):
    svc, _ = env
    session, _ = await svc.initiate(
        kb_id="kb1", folder_id=None, actor="u1", filename="f.txt",
        expected_size=10, expected_mime="text/plain", idempotency_key="ik-nostage",
    )
    # INITIATED cannot complete (no staged object).
    with pytest.raises(UploadIncomplete):
        await svc.complete(session.id)


async def test_state_machine_rejects_unknown_transition_directly():
    # Direct check: the assert_transition guard rejects illegal edges the
    # service relies on (e.g. COMMITTED -> UPLOADING is forbidden).
    from knowledge_mining.mining.contracts.state_machines import assert_transition

    with pytest.raises(IllegalTransition):
        assert_transition("upload_session", "COMMITTED", "UPLOADING")
