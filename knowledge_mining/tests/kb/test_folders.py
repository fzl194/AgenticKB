"""G2 — kb_folders 一等文件夹 CRUD（含磁盘镜像、权限、跨库隔离）。"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.folder_service import FolderService
from knowledge_mining.mining.kb.services.kb_service import Duplicate, Forbidden, NotFound

pytestmark = pytest.mark.asyncio

DOMAIN = "cloud_core_network"


async def _make_kb(db: KbDB, name: str):
    owner = await db.upsert_user_by_username(f"u_{name}")
    kb = await db.create_kb(domain=DOMAIN, name=name, owner_id=owner["id"])
    return owner["id"], kb["id"]


async def test_create_top_level_and_nested(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "kbA")

    top = await svc.create_folder(kb_id=kb_id, parent_id=None, name="5G", user_id=owner_id)
    assert top["path"] == "5G" and top["parent_id"] is None
    assert (tmp_path / kb_id / "5G").is_dir()  # 磁盘镜像

    sub = await svc.create_folder(kb_id=kb_id, parent_id=top["id"], name="AMF", user_id=owner_id)
    assert sub["path"] == "5G/AMF" and sub["parent_id"] == top["id"]
    assert (tmp_path / kb_id / "5G" / "AMF").is_dir()

    folders = await svc.list_folders(kb_id=kb_id, user_id=owner_id)
    assert {f["path"] for f in folders} == {"5G", "5G/AMF"}


async def test_create_duplicate_name(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "kbDup")
    await svc.create_folder(kb_id=kb_id, parent_id=None, name="docs", user_id=owner_id)
    with pytest.raises(Duplicate):
        await svc.create_folder(kb_id=kb_id, parent_id=None, name="docs", user_id=owner_id)


async def test_create_unsafe_name(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "kbUnsafe")
    for bad in ("a/b", "..", ".", "a\\b", " "):
        with pytest.raises(ValueError):
            await svc.create_folder(kb_id=kb_id, parent_id=None, name=bad, user_id=owner_id)


async def test_delete_empty_folder(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "kbDel")
    f = await svc.create_folder(kb_id=kb_id, parent_id=None, name="empty", user_id=owner_id)
    d = tmp_path / kb_id / "empty"
    assert d.is_dir()
    await svc.delete_folder(folder_id=f["id"], user_id=owner_id)
    assert not d.exists()
    assert await svc.list_folders(kb_id=kb_id, user_id=owner_id) == []


async def test_delete_non_empty_blocked_by_subfolder(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "kbDelSub")
    top = await svc.create_folder(kb_id=kb_id, parent_id=None, name="p", user_id=owner_id)
    await svc.create_folder(kb_id=kb_id, parent_id=top["id"], name="c", user_id=owner_id)
    with pytest.raises(ValueError):
        await svc.delete_folder(folder_id=top["id"], user_id=owner_id)


async def test_delete_non_empty_blocked_by_doc(async_pool, tmp_path):
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "kbDelDoc")
    f = await svc.create_folder(kb_id=kb_id, parent_id=None, name="hasdoc", user_id=owner_id)
    # 在该文件夹下塞一个文档身份（directory_path = folder.path）
    await db.insert_document_identity(
        domain=DOMAIN, kb_id=kb_id, document_key="doc:/hasdoc/x.md",
        document_name="x.md", storage_path=str(tmp_path / kb_id / "hasdoc" / "x.md"),
        directory_path="hasdoc", owner_id=owner_id,
    )
    with pytest.raises(ValueError):
        await svc.delete_folder(folder_id=f["id"], user_id=owner_id)


async def test_cross_kb_same_folder_name(async_pool, tmp_path):
    """两个 KB 各自建同名顶层文件夹 —— 不冲突。"""
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_a, kb_a = await _make_kb(db, "kbXa")
    owner_b, kb_b = await _make_kb(db, "kbXb")
    fa = await svc.create_folder(kb_id=kb_a, parent_id=None, name="shared_name", user_id=owner_a)
    fb = await svc.create_folder(kb_id=kb_b, parent_id=None, name="shared_name", user_id=owner_b)
    assert fa["id"] != fb["id"]
    assert {f["path"] for f in await svc.list_folders(kb_id=kb_a, user_id=owner_a)} == {"shared_name"}
    assert {f["path"] for f in await svc.list_folders(kb_id=kb_b, user_id=owner_b)} == {"shared_name"}


async def test_viewer_cannot_create(async_pool, tmp_path):
    """viewer 成员无写权限 → Forbidden。"""
    db = KbDB(async_pool)
    svc = FolderService(db, upload_root=tmp_path)
    owner_id, kb_id = await _make_kb(db, "kbPerm")
    viewer = await db.upsert_user_by_username("v_viewer")
    await db.add_member(kb_id=kb_id, user_id=viewer["id"], role="viewer")
    with pytest.raises(Forbidden):
        await svc.create_folder(kb_id=kb_id, parent_id=None, name="x", user_id=viewer["id"])
    # 不可见用户 → NotFound（不泄露存在性）
    stranger = await db.upsert_user_by_username("v_stranger")
    with pytest.raises(NotFound):
        await svc.list_folders(kb_id=kb_id, user_id=stranger["id"])
