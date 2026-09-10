"""New deployments organize object-backed documents without filesystem moves."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb import db as db_module
from knowledge_mining.mining.kb.services.folder_service import FolderService


@pytest.mark.asyncio
async def test_object_document_move_preserves_identity_and_never_creates_directory(tmp_path):
    doc = dict(id="d", kb_id="k", directory_path="old", document_name="a.pdf",
               document_key="doc:/old/a.pdf", storage_path=None, storage_object_id="o",
               source_raw_hash="h", content_revision=3)
    db = AsyncMock()
    db.get_document_identity.return_value = doc
    db.get_folder.return_value = dict(id="f", kb_id="k", path="new")
    db.move_document_logically.return_value = {**doc, "directory_path": "new"}
    svc = FolderService(db, upload_root=tmp_path)
    svc._svc._assert_write = AsyncMock()
    result = await svc.move_document(document_id="d", target_folder_id="f", user_id="u")
    assert result == {**doc, "directory_path": "new"}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_object_folder_rename_is_one_repository_operation(tmp_path):
    db = AsyncMock()
    db.get_folder.return_value = dict(id="f", kb_id="k", path="old", name="old", parent_id=None)
    db.find_folder_by_parent.return_value = None
    db.relocate_folder_logically.return_value = dict(id="f", kb_id="k", path="new", name="new")
    svc = FolderService(db, upload_root=tmp_path)
    svc._svc._assert_write = AsyncMock()
    result = await svc.rename_folder(folder_id="f", name="new", user_id="u")
    assert result["path"] == "new"
    db.relocate_folder_logically.assert_awaited_once()
    assert list(tmp_path.iterdir()) == []


class RecordingPool:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    @asynccontextmanager
    async def connection(self):
        yield self

    async def execute(self, sql, params):
        self.calls.append((sql, params))
        return self

    async def fetchone(self):
        return self.row


@pytest.mark.asyncio
async def test_replace_is_cas_and_does_not_change_document_identity():
    pool = RecordingPool({"id": "d", "content_revision": 4})
    result = await KbDB(pool).replace_document_content(
        "d", expected_revision=3, storage_object_id="new", source_raw_hash="h", file_size=12)
    assert result["content_revision"] == 4
    sql, params = pool.calls[0]
    assert "content_revision = %(revision)s" in sql
    assert "deleted_at IS NULL" in sql
    assert "content_revision = content_revision + 1" in sql
    assert "modified_at = %(modified_at)s" in sql
    assert params["modified_at"] == params["now"]
    assert "document_key =" not in sql and "directory_path =" not in sql
    assert params["revision"] == 3


@pytest.mark.asyncio
async def test_replace_conflict_returns_none_without_followup_writes():
    pool = RecordingPool()
    assert await KbDB(pool).replace_document_content(
        "d", expected_revision=3, storage_object_id="new", source_raw_hash="h", file_size=12) is None
    assert len(pool.calls) == 1
    assert "k.status = 'active'" in pool.calls[0][0]


@pytest.mark.asyncio
async def test_replacement_cas_can_bind_original_filename():
    pool = RecordingPool()
    await KbDB(pool).replace_document_content(
        "d", expected_revision=3, expected_document_name="source.pdf",
        storage_object_id="new", source_raw_hash="h", file_size=12)
    sql, params = pool.calls[0]
    assert "document_name = %(expected_name)s" in sql
    assert params["expected_name"] == "source.pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"kb_id": "other"}, {"directory_path": "moved"}, {"document_name": "renamed.pdf"}])
async def test_revival_rechecks_upload_target_after_lock(changed):
    from knowledge_mining.tests.kb.test_document_soft_delete import _kbdb
    doc = {"id": "d", "kb_id": "kb", "directory_path": "old", "document_name": "a.pdf",
           "deleted_at": "2026-01-01", **changed}
    calls = []
    db = _kbdb(calls, rows=[{"kb_id": doc["kb_id"]}, {"id": doc["kb_id"]}, doc])
    result = await db.revive_document_from_storage(
        "d", storage_object_id="new", source_raw_hash="new-hash", expected_kb_id="kb",
        expected_directory_path="old", expected_document_name="a.pdf")
    assert result is None
    assert len(calls) == 3 and all(not sql.startswith("UPDATE") for sql, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [{"expected_revision": 0}, {"file_size": -1}, {"storage_object_id": ""}, {"source_raw_hash": ""}])
async def test_invalid_replacement_never_writes(overrides):
    pool = RecordingPool()
    args = dict(expected_revision=1, storage_object_id="new", source_raw_hash="h", file_size=12)
    with pytest.raises(ValueError):
        await KbDB(pool).replace_document_content("d", **{**args, **overrides})
    assert pool.calls == []


@pytest.mark.asyncio
async def test_serving_snapshot_binds_document_as_well_as_snapshot():
    pool = RecordingPool()
    await KbDB(pool).get_current_serving_snapshot("kb", "doc")
    sql, _ = pool.calls[0]
    assert "l.document_id = t.document_id" in sql


def test_statistics_exclude_deleted_and_foreign_kb_membership():
    sql = KbDB._CURRENT_SNAPSHOT_CTE
    assert "d.deleted_at IS NULL" in sql
    assert "b.kb_id = d.kb_id" in sql


def test_outdated_knowledge_is_a_separate_active_build_fact():
    import inspect
    assert "s.raw_content_hash AS serving_raw_hash" in db_module._KB_BUILD_JOIN_SQL
    expression = db_module._KNOWLEDGE_OUTDATED_SQL
    assert "kbmv.in_active_build" in expression
    assert "kbmv.serving_raw_hash <> d.source_raw_hash" in expression
    assert "NULLIF" in expression and "FALSE" in expression
    assert "AS knowledge_outdated" in inspect.getsource(KbDB.list_documents_in_kb)
    assert "AS knowledge_outdated" in inspect.getsource(KbDB.get_document_identity)
    assert "knowledge_outdated" not in db_module._STATUS_CASE_SQL
