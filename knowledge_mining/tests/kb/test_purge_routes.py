# -*- coding: utf-8 -*-
"""删除体系路由测试（2026-09-16 定稿：软删退役、统一硬删管线）.

纯 TestClient + 依赖覆盖（无 PG）：验证确认门槛/权限分派/管线调用契约。
管线本身的 SQL 正确性由 test_purge_e2e_real_pg.py 在真库上验证。
"""
from __future__ import annotations

import sys
from typing import Any

import pytest

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.deps import (
    get_kb_db, get_kb_service, get_purge_service,
)
from knowledge_mining.mining.kb.routes import documents as doc_routes
from knowledge_mining.mining.kb.routes import folders as folder_routes
from knowledge_mining.mining.kb.routes import kbs as kb_routes

OWNER = {"id": "u-owner", "username": "owner", "site_role": "member"}
ADMIN = {"id": "u-admin", "username": "admin", "site_role": "admin"}
VIEWER = {"id": "u-viewer", "username": "viewer", "site_role": "member"}

KB = {"id": "kb-1", "domain": "d1", "name": "交付局知识库", "owner_id": "u-owner",
      "visibility": "private", "status": "active", "deleted_at": None}
KB_DELETED = {"id": "kb-old", "domain": "d1", "name": "旧公共库", "owner_id": "u-owner",
              "visibility": "private", "status": "deleted", "deleted_at": "t"}


class FakePurge:
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.busy = False            # True 时 purge_* 抛 kb_busy（门禁测试用）
        self.preview_documents = 12  # 预览计数（确认门槛测试用）

    async def assert_kb_idle(self, kb_id):
        if self.busy:
            from knowledge_mining.mining.kb.services.purge_service import PurgeError
            raise PurgeError(f"kb_busy: {kb_id} 有排队/执行中的挖掘任务")

    async def purge_kb(self, kb_id):
        await self.assert_kb_idle(kb_id)
        self.calls.append(("purge_kb", (kb_id,), {}))
        return {"deleted_documents": ["d1", "d2"], "reclaimed_snapshots": 1,
                "skipped_shared_snapshots": [], "reclaimed_objects": 1,
                "skipped_shared_objects": []}

    async def purge_folder(self, kb_id, path):
        await self.assert_kb_idle(kb_id)
        self.calls.append(("purge_folder", (kb_id, path), {}))
        return {"deleted_documents": ["d3"], "reclaimed_snapshots": 0,
                "skipped_shared_snapshots": [], "reclaimed_objects": 0,
                "skipped_shared_objects": [], "removed_folders": 3}

    async def purge_documents(self, kb_id, document_ids, *, assert_kb=True):
        if assert_kb:
            await self.assert_kb_idle(kb_id)
        self.calls.append(("purge_documents", (kb_id, tuple(document_ids)),
                           {"assert_kb": assert_kb}))
        return {"deleted_documents": list(document_ids), "reclaimed_snapshots": 0,
                "skipped_shared_snapshots": [], "reclaimed_objects": 0,
                "skipped_shared_objects": []}

    async def folder_delete_preview(self, kb_id, path):
        return {"folders": 3, "documents": self.preview_documents}

    async def deprecate_superseded_snapshots(self):
        return {"deprecated": 2}

    async def reclaim_deprecated_snapshots(self):
        return {"reclaimed": 1, "reclaimed_objects": 1}


class FakeKbDb:
    def __init__(self):
        self.deleted_listed = False

    async def get_kb(self, kb_id, *, include_deleted=False):
        if kb_id == KB["id"]:
            return KB
        if kb_id == KB_DELETED["id"] and include_deleted:
            return KB_DELETED
        return None

    async def can_restore(self, *, kb_id, user_id):
        return user_id == OWNER["id"] or user_id == ADMIN["id"]

    async def list_deleted_kbs(self, *, domain):
        self.deleted_listed = True
        return [KB_DELETED]

    async def get_folder(self, folder_id):
        if folder_id == "f1":
            return {"id": "f1", "kb_id": KB["id"], "name": "02 特性配置",
                    "path": "02 特性配置"}
        return None


class FakeKbService:
    async def list_visible(self, *, user_id, domain):
        return [KB]


class FakeDocService:
    class _Svc:
        @staticmethod
        async def _assert_write(kb_id, user_id):
            if user_id == VIEWER["id"]:
                from knowledge_mining.mining.kb.services.kb_service import Forbidden
                raise Forbidden(kb_id)
    _svc = _Svc()


def _client(user: dict):
    app = FastAPI()
    app.include_router(kb_routes.router)
    app.include_router(folder_routes.router)
    app.include_router(doc_routes.router)
    purge = FakePurge()
    kbdb = FakeKbDb()

    async def _user():
        return user
    app.dependency_overrides[current_user] = _user
    from knowledge_mining.mining.kb.auth import require_admin

    async def _admin():
        if user.get("site_role") != "admin":
            from fastapi import HTTPException
            raise HTTPException(403, "admin required")
        return user
    app.dependency_overrides[require_admin] = _admin
    app.dependency_overrides[get_purge_service] = lambda: purge
    app.dependency_overrides[get_kb_db] = lambda: kbdb
    app.dependency_overrides[get_kb_service] = lambda: FakeKbService()
    from knowledge_mining.mining.kb.deps import get_document_service
    app.dependency_overrides[get_document_service] = lambda: FakeDocService()
    from knowledge_mining.mining.kb.deps import get_folder_service
    from knowledge_mining.mining.kb.services.folder_service import FolderService

    class FakeFolderSvc:
        _db = kbdb
        _svc = FakeDocService._Svc
    app.dependency_overrides[get_folder_service] = lambda: FakeFolderSvc()
    return TestClient(app), purge, kbdb


# ---------------------------------------------------------------- KB 硬删

def test_kb_delete_requires_confirm_name():
    c, purge, _ = _client(OWNER)
    assert c.delete("/api/kb/kb-1").status_code == 422
    assert c.request("DELETE", "/api/kb/kb-1", json={"confirm_name": "错名"}).status_code == 422
    assert purge.calls == []


def test_kb_delete_hard_purges_on_name_match():
    c, purge, _ = _client(OWNER)
    resp = c.request("DELETE", "/api/kb/kb-1", json={"confirm_name": "交付局知识库"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["deleted_documents"] == 2            # 数量，非 id 清单
    assert purge.calls == [("purge_kb", ("kb-1",), {})]


def test_kb_delete_accepts_soft_deleted_kb_for_cleanup():
    """软删态库可彻底删除（内网存量善后入口）."""
    c, purge, _ = _client(OWNER)
    resp = c.request("DELETE", "/api/kb/kb-old", json={"confirm_name": "旧公共库"})
    assert resp.status_code == 200
    assert purge.calls == [("purge_kb", ("kb-old",), {})]


def test_kb_delete_404_for_non_owner():
    c, purge, _ = _client(VIEWER)                    # can_restore=False
    assert c.request("DELETE", "/api/kb/kb-1", json={"confirm_name": "交付局知识库"}).status_code == 404
    assert purge.calls == []


def test_list_deleted_kbs_admin_only():
    _, _, kbdb_admin = _client(ADMIN)
    # 非 admin 拒绝
    c2, _, kbdb2 = _client(VIEWER)
    assert c2.get("/api/kb", params={"domain": "d1", "include_deleted": True}).status_code == 403
    assert not kbdb2.deleted_listed


def test_list_deleted_kbs_admin_ok():
    c, _, kbdb = _client(ADMIN)
    resp = c.get("/api/kb", params={"domain": "d1", "include_deleted": True})
    assert resp.status_code == 200
    assert resp.json() == [KB_DELETED]
    assert kbdb.deleted_listed


def test_list_normal_still_works():
    c, _, _ = _client(VIEWER)
    resp = c.get("/api/kb", params={"domain": "d1"})
    assert resp.status_code == 200
    assert resp.json() == [KB]


# ---------------------------------------------------------------- 文件夹级联

def test_folder_delete_preview_and_cascade():
    c, purge, _ = _client(OWNER)
    resp = c.get("/api/kb/kb-1/folders/f1/delete-preview")
    assert resp.status_code == 200
    assert resp.json() == {"folders": 3, "documents": 12}
    resp = c.delete("/api/kb/kb-1/folders/f1")
    assert resp.status_code == 200
    assert resp.json()["removed_folders"] == 3
    assert purge.calls == [("purge_folder", ("kb-1", "02 特性配置"), {})]


def test_folder_delete_viewer_forbidden():
    c, purge, _ = _client(VIEWER)
    assert c.delete("/api/kb/kb-1/folders/f1").status_code == 403
    assert purge.calls == []


def test_folder_delete_404_cross_kb():
    c, _, _ = _client(OWNER)
    assert c.delete("/api/kb/kb-other/folders/f1").status_code == 404


# ---------------------------------------------------------------- 文档批量

def test_documents_purge_batch():
    c, purge, _ = _client(OWNER)
    resp = c.post("/api/kb/kb-1/documents/purge",
                  json={"document_ids": ["d1", "d2"]})
    assert resp.status_code == 200
    assert resp.json()["deleted_documents"] == 2
    assert purge.calls == [("purge_documents", ("kb-1", ("d1", "d2")),
                            {"assert_kb": True})]


def test_documents_purge_batch_rejects_empty_and_viewer():
    c, purge, _ = _client(OWNER)
    assert c.post("/api/kb/kb-1/documents/purge",
                  json={"document_ids": []}).status_code == 422
    c2, purge2, _ = _client(VIEWER)
    assert c2.post("/api/kb/kb-1/documents/purge",
                   json={"document_ids": ["d1"]}).status_code == 403
    assert purge2.calls == []


def test_document_single_delete_routes_to_purge():
    c, purge, _ = _client(OWNER)
    resp = c.delete("/api/kb/kb-1/documents/d9")
    assert resp.status_code == 200
    assert purge.calls == [("purge_documents", ("kb-1", ("d9",)),
                            {"assert_kb": True})]


# ---------------------------------------------------------------- GC

def test_snapshot_gc_admin_endpoint():
    c, _, _ = _client(ADMIN)
    resp = c.post("/api/kb/snapshots/gc")
    assert resp.status_code == 200
    assert resp.json() == {"deprecated": 2, "reclaimed": 1, "reclaimed_objects": 1}


# ------------------------------------------------- 审查修复回归

def test_folder_delete_large_scope_requires_confirm_name():
    """>50 文档的级联删除必须 body.confirm_name == 文件夹名（安全审查 H-1）."""
    c, purge, _ = _client(OWNER)
    purge.preview_documents = 51
    # 无确认 → 422；错名 → 422
    assert c.delete("/api/kb/kb-1/folders/f1").status_code == 422
    assert c.request("DELETE", "/api/kb/kb-1/folders/f1",
                     json={"confirm_name": "错"}).status_code == 422
    assert purge.calls == []
    # 正确文件夹名 → 放行
    resp = c.request("DELETE", "/api/kb/kb-1/folders/f1",
                     json={"confirm_name": "02 特性配置"})
    assert resp.status_code == 200
    assert purge.calls == [("purge_folder", ("kb-1", "02 特性配置"), {})]


def test_purge_busy_run_rejected_409(monkeypatch):
    """在途挖掘 Run 门禁（审查 M-2）：kb_busy → 409，不删分毫."""
    c, purge, _ = _client(OWNER)
    purge.busy = True
    resp = c.request("DELETE", "/api/kb/kb-1", json={"confirm_name": "交付局知识库"})
    assert resp.status_code == 409
    assert "kb_busy" in resp.json()["detail"]
    assert purge.calls == []
    resp = c.post("/api/kb/kb-1/documents/purge", json={"document_ids": ["d1"]})
    assert resp.status_code == 409
    assert purge.calls == []


def test_documents_purge_long_id_rejected_by_schema():
    """入参解析期收紧（审查 M-3）：超长 id / 超量列表 → 422."""
    c, _, _ = _client(OWNER)
    assert c.post("/api/kb/kb-1/documents/purge",
                  json={"document_ids": ["x" * 65]}).status_code == 422
    assert c.post("/api/kb/kb-1/documents/purge",
                  json={"document_ids": [f"d{i}" for i in range(5001)]}).status_code == 422
