"""Unit tests for the in-memory fake repositories (M1.2).

These verify the fakes in isolation: basic CRUD round-trips, optimistic
concurrency on quota + document revision, and the dedup probe. The service
tests exercise the fakes end-to-end; this file pins down the fake semantics
the services rely on.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from knowledge_mining.mining.contracts.file_management import (
    DocumentRevisionConflict,
    FileAuditEvent,
    QuotaExceeded,
    StorageObjectRecord,
)
from knowledge_mining.mining.contracts.state_machines import IllegalTransition
from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryDocumentCurrentContentRepository,
    MemoryFileAuditRepository,
    MemoryQuotaRepository,
    MemoryStorageObjectRepository,
)


# ---------------------------------------------------------------------------
# StorageObjectRepository
# ---------------------------------------------------------------------------


def _obj(id_: str = "o1", key: str = "k1") -> StorageObjectRecord:
    return StorageObjectRecord(
        id=id_, provider="fake", bucket="src", object_key=key,
        object_version_id=None, sha256="h", size=1, mime="text/plain",
        artifact_class="source", state="STAGING",
    )


@pytest.mark.asyncio
async def test_storage_object_register_and_get():
    repo = MemoryStorageObjectRepository()
    rec = await repo.register(_obj())
    assert rec.id == "o1"
    assert await repo.get("o1") == rec


@pytest.mark.asyncio
async def test_storage_object_find_by_location_dedup():
    repo = MemoryStorageObjectRepository()
    await repo.register(_obj(id_="o1", key="k1"))
    found = await repo.find_by_location("src", "k1", None)
    assert found is not None
    assert found.id == "o1"
    assert await repo.find_by_location("src", "missing", None) is None


@pytest.mark.asyncio
async def test_storage_object_find_by_location_null_version_normalized():
    repo = MemoryStorageObjectRepository()
    await repo.register(_obj(id_="o1", key="k1"))  # version_id None
    # Probe with explicit None and "" both resolve to the same record.
    assert (await repo.find_by_location("src", "k1", None)) is not None
    assert (await repo.find_by_location("src", "k1", "")) is not None


@pytest.mark.asyncio
async def test_storage_object_set_state_validates_transition():
    repo = MemoryStorageObjectRepository()
    await repo.register(_obj(id_="o1"))  # state STAGING
    await repo.set_state("o1", "AVAILABLE")
    assert (await repo.get("o1")).state == "AVAILABLE"
    # Illegal: AVAILABLE -> STAGING is not an edge.
    with pytest.raises(IllegalTransition):
        await repo.set_state("o1", "STAGING")


@pytest.mark.asyncio
async def test_storage_object_mark_verified():
    repo = MemoryStorageObjectRepository()
    await repo.register(_obj(id_="o1"))
    await repo.mark_verified("o1", "2026-01-01T00:00:00Z")
    assert (await repo.get("o1")).last_verified_at == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# DocumentCurrentContentRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_create_then_set_current_content_advances_revision():
    repo = MemoryDocumentCurrentContentRepository()
    doc = await repo.create_document(
        kb_id="kb1", document_id="d1", folder_id=None, owner_id="u1",
        document_name="f.txt", document_type=None,
        storage_object_id="o1", source_raw_hash="h1",
    )
    assert doc.content_revision == 1
    updated = await repo.set_current_content(
        "d1", "o2", "h2", expected_revision=1,
    )
    assert updated.content_revision == 2
    assert updated.storage_object_id == "o2"


@pytest.mark.asyncio
async def test_document_set_current_content_stale_revision_raises_conflict():
    repo = MemoryDocumentCurrentContentRepository()
    await repo.create_document(
        kb_id="kb1", document_id="d1", folder_id=None, owner_id="u1",
        document_name="f.txt", document_type=None,
        storage_object_id="o1", source_raw_hash="h1",
    )
    await repo.set_current_content("d1", "o2", "h2", expected_revision=1)
    with pytest.raises(DocumentRevisionConflict):
        await repo.set_current_content("d1", "o3", "h3", expected_revision=1)


@pytest.mark.asyncio
async def test_document_create_rejects_duplicate_id():
    repo = MemoryDocumentCurrentContentRepository()
    await repo.create_document(
        kb_id="kb1", document_id="d1", folder_id=None, owner_id="u1",
        document_name="f", document_type=None,
        storage_object_id="o1", source_raw_hash="h1",
    )
    with pytest.raises(ValueError):
        await repo.create_document(
            kb_id="kb1", document_id="d1", folder_id=None, owner_id="u1",
            document_name="f", document_type=None,
            storage_object_id="o2", source_raw_hash="h2",
        )


# ---------------------------------------------------------------------------
# FileAuditRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_append_assigns_id_and_created_at():
    repo = MemoryFileAuditRepository()
    event = await repo.append(
        FileAuditEvent(
            id="", kb_id="kb1", document_id="d1", storage_object_id="o1",
            content_revision=1, actor="u1", action="upload",
        )
    )
    assert event.id  # assigned
    assert event.created_at  # assigned
    assert len(repo.all()) == 1


@pytest.mark.asyncio
async def test_audit_by_document_filters():
    repo = MemoryFileAuditRepository()
    await repo.append(FileAuditEvent(
        id="a1", kb_id="kb1", document_id="d1", storage_object_id="o1",
        content_revision=1, actor="u1", action="upload",
    ))
    await repo.append(FileAuditEvent(
        id="a2", kb_id="kb1", document_id="d2", storage_object_id="o2",
        content_revision=1, actor="u1", action="upload",
    ))
    assert len(repo.by_document("d1")) == 1
    assert len(repo.by_document("d2")) == 1
    assert len(repo.by_document("d3")) == 0


# ---------------------------------------------------------------------------
# QuotaRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quota_get_returns_zero_limit_default_for_unknown_kb():
    repo = MemoryQuotaRepository()
    q = await repo.get("unknown")
    assert q.limit_bytes == 0
    assert q.version == 1


@pytest.mark.asyncio
async def test_quota_reserve_commit_release_lifecycle():
    repo = MemoryQuotaRepository()
    repo.seed("kb1", 1000)

    q1 = await repo.reserve("kb1", 300, expected_version=1)
    assert q1.reserved_bytes == 300
    assert q1.version == 2

    q2 = await repo.commit("kb1", 300, 300, expected_version=2)
    assert q2.reserved_bytes == 0
    assert q2.used_bytes == 300
    assert q2.version == 3

    q3 = await repo.reserve("kb1", 100, expected_version=3)
    assert q3.reserved_bytes == 100
    q4 = await repo.release("kb1", 100, expected_version=4)
    assert q4.reserved_bytes == 0


@pytest.mark.asyncio
async def test_quota_reserve_over_limit_raises():
    repo = MemoryQuotaRepository()
    repo.seed("kb1", 100)
    with pytest.raises(QuotaExceeded):
        await repo.reserve("kb1", 101, expected_version=1)


@pytest.mark.asyncio
async def test_quota_reserve_stale_version_raises_conflict():
    repo = MemoryQuotaRepository()
    repo.seed("kb1", 1000)
    await repo.reserve("kb1", 100, expected_version=1)
    with pytest.raises(ValueError):
        await repo.reserve("kb1", 100, expected_version=1)  # stale


@pytest.mark.asyncio
async def test_quota_release_underflow_guarded():
    repo = MemoryQuotaRepository()
    repo.seed("kb1", 1000)
    await repo.reserve("kb1", 50, expected_version=1)
    with pytest.raises(ValueError):
        await repo.release("kb1", 100, expected_version=2)  # would go negative
