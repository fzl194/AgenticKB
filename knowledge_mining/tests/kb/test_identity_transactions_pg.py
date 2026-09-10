"""Real PostgreSQL tests; shared fixtures enforce a disposable *_test database."""
import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from psycopg.errors import UniqueViolation

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.folder_service import FolderService
from knowledge_mining.mining.kb.services.kb_service import Duplicate
from knowledge_mining.tests.kb.test_stats_queries import _build_snapshot

pytestmark = pytest.mark.asyncio
DOMAIN = "cloud_core_network"


async def setup_kb(pool):
    db = KbDB(pool)
    owner = await db.upsert_user_by_username("identity-owner")
    kb = await db.create_kb(domain=DOMAIN, name="identity", owner_id=owner["id"])
    return db, kb["id"], owner["id"]


async def add_doc(db, kb_id, name="a.pdf", directory="", key=None):
    return await db.insert_document_from_storage(
        domain=DOMAIN, kb_id=kb_id, document_key=key or "doc:/" + directory + "/" + name,
        document_name=name, directory_path=directory, storage_object_id="object-one",
        source_raw_hash="original-hash", file_size=10)


async def test_concurrent_same_location_allows_only_one_identity(async_pool):
    db, kb, _ = await setup_kb(async_pool)
    results = await asyncio.gather(
        add_doc(db, kb, key="key-1"), add_doc(db, kb, key="key-2"), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, UniqueViolation) for r in results) == 1


async def test_move_collision_rolls_back_and_preserves_content(async_pool, tmp_path):
    db, kb, owner = await setup_kb(async_pool)
    svc = FolderService(db, tmp_path)
    target = await svc.create_folder(kb_id=kb, parent_id=None, name="target", user_id=owner)
    doc = await add_doc(db, kb)
    await add_doc(db, kb, directory="target")
    with pytest.raises(Duplicate):
        await svc.move_document(document_id=doc["id"], target_folder_id=target["id"], user_id=owner)
    actual = await db.get_document_identity(doc["id"])
    for key in ("directory_path", "document_key", "storage_object_id", "source_raw_hash", "content_revision"):
        assert actual[key] == doc[key]


async def test_folder_prefixes_are_literal_and_content_is_unchanged(async_pool, tmp_path):
    db, kb, owner = await setup_kb(async_pool)
    svc = FolderService(db, tmp_path)
    a = await svc.create_folder(kb_id=kb, parent_id=None, name="a_%", user_id=owner)
    await svc.create_folder(kb_id=kb, parent_id=None, name="abc", user_id=owner)
    first = await add_doc(db, kb, directory="a_%")
    second = await add_doc(db, kb, directory="abc")
    await svc.rename_folder(folder_id=a["id"], name="renamed", user_id=owner)
    moved = await db.get_document_identity(first["id"])
    assert moved["directory_path"] == "renamed"
    assert moved["folder_id"] == a["id"]
    assert moved["document_key"] == first["document_key"]
    assert moved["storage_object_id"] == first["storage_object_id"]
    assert moved["content_revision"] == 1
    assert (await db.get_document_identity(second["id"]))["directory_path"] == "abc"


async def test_replace_cas_has_one_winner_and_deleted_is_rejected(async_pool):
    db, kb, _ = await setup_kb(async_pool)
    doc = await add_doc(db, kb)
    args = dict(expected_revision=1, storage_object_id="object-two", source_raw_hash="new-hash", file_size=20)
    results = await asyncio.gather(db.replace_document_content(doc["id"], **args), db.replace_document_content(doc["id"], **args))
    assert sum(r is not None for r in results) == 1
    await db.soft_delete_document(doc["id"])
    assert await db.replace_document_content(doc["id"], **{**args, "expected_revision": 2}) is None


async def test_stats_ignore_deleted_and_foreign_builds(async_pool):
    db, kb, owner = await setup_kb(async_pool)
    other = await db.create_kb(domain=DOMAIN, name="foreign", owner_id=owner)
    doc = await add_doc(db, kb)
    await _build_snapshot(async_pool, kb_id=kb, document_id=doc["id"], segments=2)
    await _build_snapshot(async_pool, kb_id=other["id"], document_id=doc["id"], segments=4, created_at="2026-02-01T00:00:00+00:00")
    assert (await db.stats_assets(kb_ids=[kb, other["id"]]))["segments"] == 2
    await db.soft_delete_document(doc["id"])
    assert (await db.stats_assets(kb_ids=[kb]))["segments"] == 0


async def test_shared_snapshot_uses_own_document_source(async_pool):
    db, kb, _ = await setup_kb(async_pool)
    first, second = await add_doc(db, kb), await add_doc(db, kb, name="b.pdf")
    _, snapshot = await _build_snapshot(async_pool, kb_id=kb, document_id=first["id"])
    async with async_pool.connection() as conn:
        for doc, revision, linked in [(first, 1, "2026-01-01"), (second, 7, "2026-02-01")]:
            await conn.execute(
                """INSERT INTO asset_document_snapshot_links
                   (id, document_id, document_snapshot_id, relative_path, source_uri, linked_at,
                    source_storage_object_id, source_content_revision)
                   VALUES (%s, %s, %s, 'a.pdf', 'object://source', %s, %s, %s)""",
                [uuid.uuid4().hex, doc["id"], snapshot, linked, "source-" + doc["id"], revision])
    current = await db.get_current_serving_snapshot(kb, first["id"])
    assert current["source_content_revision"] == 1
    assert current["source_storage_object_id"] == "source-" + first["id"]


async def test_subtree_failure_rolls_back_folder_and_document_positions(async_pool, tmp_path):
    db, kb, owner = await setup_kb(async_pool)
    svc = FolderService(db, tmp_path)
    folder = await svc.create_folder(kb_id=kb, parent_id=None, name="original", user_id=owner)
    doc = await add_doc(db, kb, directory="original")

    class FailingPool:
        @asynccontextmanager
        async def connection(self):
            async with async_pool.connection() as conn:
                class Proxy:
                    async def execute(self, sql, params):
                        if "UPDATE asset_documents SET directory_path" in sql:
                            raise RuntimeError("injected failure after folder updates")
                        return await conn.execute(sql, params)
                yield Proxy()

    with pytest.raises(RuntimeError, match="injected failure"):
        await KbDB(FailingPool()).relocate_folder_logically(
            folder["id"], kb_id=kb, name="changed", parent_id=None, expected_path="original")
    assert (await db.get_folder(folder["id"]))["path"] == "original"
    assert (await db.get_document_identity(doc["id"]))["directory_path"] == "original"


async def test_rename_and_restore_cannot_create_duplicate_locations(async_pool):
    db, kb, _ = await setup_kb(async_pool)
    first, second = await add_doc(db, kb), await add_doc(db, kb, name="b.pdf")
    with pytest.raises(UniqueViolation):
        await db.update_document_identity(second["id"], document_name="a.pdf")
    await db.soft_delete_document(first["id"])
    await db.update_document_identity(second["id"], document_name="a.pdf")
    with pytest.raises(UniqueViolation):
        await db.clear_document_deleted(first["id"])
    assert (await db.get_document_identity(first["id"], include_deleted=True))["deleted_at"] is not None


async def test_outdated_fact_tracks_serving_hash_without_changing_status(async_pool):
    db, kb, _ = await setup_kb(async_pool)
    doc = await add_doc(db, kb)
    initial = await db.get_document_identity(doc["id"])
    assert initial["status"] == "uploaded" and initial["knowledge_outdated"] is False
    _, original = await _build_snapshot(async_pool, kb_id=kb, document_id=doc["id"])
    async with async_pool.connection() as conn:
        await conn.execute("UPDATE asset_document_snapshots SET raw_content_hash = %s WHERE id = %s", [doc["source_raw_hash"], original])
    assert (await db.get_document_identity(doc["id"]))["knowledge_outdated"] is False
    await db.replace_document_content(doc["id"], expected_revision=1, storage_object_id="new-object", source_raw_hash="new-hash", file_size=20)
    assert (await db.get_document_identity(doc["id"]))["knowledge_outdated"] is True
    assert (await db.list_documents_in_kb(kb_id=kb))[0]["knowledge_outdated"] is True
    _, fresh = await _build_snapshot(async_pool, kb_id=kb, document_id=doc["id"], build_status="failed", created_at="2026-02-01")
    async with async_pool.connection() as conn:
        await conn.execute("UPDATE asset_document_snapshots SET raw_content_hash = 'new-hash' WHERE id = %s", [fresh])
    assert (await db.get_document_identity(doc["id"]))["knowledge_outdated"] is True
    async with async_pool.connection() as conn:
        await conn.execute("UPDATE asset_builds SET status = 'validated' WHERE id IN (SELECT build_id FROM asset_build_document_snapshots WHERE document_snapshot_id = %s)", [fresh])
    assert (await db.get_document_identity(doc["id"]))["knowledge_outdated"] is False
    assert (await db.list_documents_in_kb(kb_id=kb))[0]["knowledge_outdated"] is False


async def test_logical_move_keeps_folder_id_and_path_consistent(async_pool, tmp_path):
    db, kb, owner = await setup_kb(async_pool)
    svc = FolderService(db, tmp_path)
    folder = await svc.create_folder(kb_id=kb, parent_id=None, name="f", user_id=owner)
    doc = await add_doc(db, kb)
    await svc.move_document(document_id=doc["id"], target_folder_id=folder["id"], user_id=owner)
    moved = await db.get_document_identity(doc["id"])
    assert moved["folder_id"] == folder["id"] and moved["directory_path"] == "f"
    await svc.move_document(document_id=doc["id"], target_folder_id=None, user_id=owner)
    root = await db.get_document_identity(doc["id"])
    assert root["folder_id"] is None and root["directory_path"] == ""


async def test_replacement_rejects_rename_that_happened_during_upload(async_pool):
    db, kb, _ = await setup_kb(async_pool)
    doc = await add_doc(db, kb)
    await db.update_document_identity(doc["id"], document_name="renamed.txt")
    result = await db.replace_document_content(
        doc["id"], expected_revision=1, expected_document_name="a.pdf",
        storage_object_id="new-object", source_raw_hash="new-hash", file_size=20)
    assert result is None
    current = await db.get_document_identity(doc["id"])
    assert current["source_raw_hash"] == doc["source_raw_hash"]
    assert current["content_revision"] == 1 and current["document_name"] == "renamed.txt"


async def test_revival_rejects_parent_move_that_happened_during_upload(async_pool, tmp_path):
    db, kb, owner = await setup_kb(async_pool)
    svc = FolderService(db, tmp_path)
    folder = await svc.create_folder(kb_id=kb, parent_id=None, name="old", user_id=owner)
    doc = await add_doc(db, kb, directory="old")
    await db.soft_delete_document(doc["id"])
    await svc.rename_folder(folder_id=folder["id"], name="new", user_id=owner)
    result = await db.revive_document_from_storage(
        doc["id"], storage_object_id="new-object", source_raw_hash="new-hash", file_size=20,
        expected_kb_id=kb, expected_directory_path="old", expected_document_name="a.pdf")
    assert result is None
    current = await db.get_document_identity(doc["id"], include_deleted=True)
    assert current["deleted_at"] is not None and current["source_raw_hash"] == doc["source_raw_hash"]
    assert current["directory_path"] == "new"
