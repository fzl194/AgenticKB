"""G3 — 移动 / 改名（文件 + 文件夹子树）。对象文件的身份键不变，只改逻辑位置。"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.folder_service import FolderService
from knowledge_mining.mining.kb.services.kb_service import Duplicate, Forbidden, NotFound
from knowledge_mining.mining.kb.storage import build_document_key

pytestmark = pytest.mark.asyncio

DOMAIN = "cloud_core_network"


async def _make_kb(db: KbDB, name: str):
    owner = await db.upsert_user_by_username(f"m_{name}")
    kb = await db.create_kb(domain=DOMAIN, name=name, owner_id=owner["id"])
    return owner["id"], kb["id"]


async def _make_doc(db, kb_id, directory_path, name, owner_id, tmp_path):
    """Object-backed identity: no local source path exists."""
    doc = await db.insert_document_from_storage(
        domain=DOMAIN, kb_id=kb_id,
        document_key=build_document_key(directory_path, name), document_name=name,
        storage_object_id="object-" + name, source_raw_hash="hash-" + name,
        directory_path=directory_path or "", owner_id=owner_id,
    )
    return doc["id"], doc["document_key"]


async def test_rename_folder_relocates_subtree(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "mvRename")
    p = await svc.create_folder(kb_id=kb_id, parent_id=None, name="p", user_id=owner_id)
    c = await svc.create_folder(kb_id=kb_id, parent_id=p["id"], name="c", user_id=owner_id)
    d_p_id, k_p = await _make_doc(db, kb_id, "p", "a.md", owner_id, tmp_path)
    d_c_id, k_c = await _make_doc(db, kb_id, "p/c", "b.md", owner_id, tmp_path)

    await svc.rename_folder(folder_id=p["id"], name="q", user_id=owner_id)

    # 文件夹路径重写
    folders = {f["id"]: f for f in await svc.list_folders(kb_id=kb_id, user_id=owner_id)}
    assert folders[p["id"]]["path"] == "q" and folders[p["id"]]["name"] == "q"
    assert folders[c["id"]]["path"] == "q/c"
    # 文档位置改变，稳定身份键不变
    dp = await db.get_document_identity(d_p_id)
    dc = await db.get_document_identity(d_c_id)
    assert dp["directory_path"] == "q" and dp["storage_path"] is None
    assert dc["directory_path"] == "q/c" and dc["storage_path"] is None
    assert dp["document_key"] == k_p and dc["document_key"] == k_c
    # 对象存储不做磁盘迁移
    assert list(tmp_path.iterdir()) == []


async def test_move_folder_into_another(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "mvInto")
    a = await svc.create_folder(kb_id=kb_id, parent_id=None, name="a", user_id=owner_id)
    b = await svc.create_folder(kb_id=kb_id, parent_id=None, name="b", user_id=owner_id)
    d_id, _ = await _make_doc(db, kb_id, "a", "x.md", owner_id, tmp_path)

    await svc.move_folder(folder_id=a["id"], target_parent_id=b["id"], user_id=owner_id)

    folders = {f["id"]: f for f in await svc.list_folders(kb_id=kb_id, user_id=owner_id)}
    assert folders[a["id"]]["path"] == "b/a" and folders[a["id"]]["parent_id"] == b["id"]
    doc = await db.get_document_identity(d_id)
    assert doc["directory_path"] == "b/a"


async def test_move_folder_cycle_blocked(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "mvCycle")
    p = await svc.create_folder(kb_id=kb_id, parent_id=None, name="p", user_id=owner_id)
    c = await svc.create_folder(kb_id=kb_id, parent_id=p["id"], name="c", user_id=owner_id)
    with pytest.raises(ValueError):  # 把 p 移进自己的子孙 c
        await svc.move_folder(folder_id=p["id"], target_parent_id=c["id"], user_id=owner_id)
    with pytest.raises(ValueError):  # 移进自己
        await svc.move_folder(folder_id=p["id"], target_parent_id=p["id"], user_id=owner_id)


async def test_move_document_into_folder(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "mvDoc")
    f = await svc.create_folder(kb_id=kb_id, parent_id=None, name="f", user_id=owner_id)
    d_id, key = await _make_doc(db, kb_id, "", "root.md", owner_id, tmp_path)

    await svc.move_document(document_id=d_id, target_folder_id=f["id"], user_id=owner_id)

    doc = await db.get_document_identity(d_id)
    assert doc["directory_path"] == "f"
    assert doc["storage_path"] is None
    assert doc["document_key"] == key

    # 移回根（target_folder_id=None），key 始终不变
    await svc.move_document(document_id=d_id, target_folder_id=None, user_id=owner_id)
    doc2 = await db.get_document_identity(d_id)
    assert doc2["directory_path"] == ""
    assert doc2["document_key"] == "doc:/root.md"


async def test_move_viewer_forbidden(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "mvPerm")
    f = await svc.create_folder(kb_id=kb_id, parent_id=None, name="f", user_id=owner_id)
    d_id, _ = await _make_doc(db, kb_id, "", "y.md", owner_id, tmp_path)
    viewer = await db.upsert_user_by_username("mv_viewer")
    await db.add_member(kb_id=kb_id, user_id=viewer["id"], role="viewer")
    with pytest.raises(Forbidden):
        await svc.move_document(document_id=d_id, target_folder_id=f["id"], user_id=viewer["id"])


async def test_rename_duplicate_blocked(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "mvDup")
    await svc.create_folder(kb_id=kb_id, parent_id=None, name="a", user_id=owner_id)
    b = await svc.create_folder(kb_id=kb_id, parent_id=None, name="b", user_id=owner_id)
    with pytest.raises(Duplicate):
        await svc.rename_folder(folder_id=b["id"], name="a", user_id=owner_id)
