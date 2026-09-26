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
#: d1 的域管理员（RBAC：生命周期管理放宽到本域域管理员；跨域仍拒）
DOMAIN_ADMIN = {"id": "u-dadmin", "username": "dadmin", "site_role": "member"}

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

    async def can_manage_domain(self, *, user_id, domain):
        # 站点管理员通行；域管理员仅本域（d1）——跨域域管理员（查 d2）拒绝
        return user_id == ADMIN["id"] or (
            user_id == DOMAIN_ADMIN["id"] and domain == "d1")

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


class FakeTaskRepo:
    tasks: list[dict] = []

    def __init__(self, pool=None):
        pass

    async def get_active_task(self, kb_id):
        for t in reversed(FakeTaskRepo.tasks):
            if t["kb_id"] == kb_id and t["status"] in ("queued", "running"):
                return t
        return None

    async def list_tasks(self, *, domain):
        out = []
        for t in FakeTaskRepo.tasks:
            row = dict(t)
            row["progress"] = row.get("progress_json", {})
            out.append(row)
        return out


def _fake_start_purge_task(**kw):
    import asyncio

    async def _noop_progress(phase, **counts):
        pass

    row = {"id": f"task-{len(FakeTaskRepo.tasks) + 1}", "kb_id": kw["kb_id"],
           "kb_name": kw["kb_name"], "domain": kw["domain"],
           "status": "queued", "phase": "queued", "progress_json": {},
           "requested_by": kw["requested_by"], "error": None,
           "created_at": "t", "updated_at": "t"}
    FakeTaskRepo.tasks.append(row)
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return _async_return(row)


class _AsyncReturn:
    def __init__(self, value):
        self._value = value

    def __await__(self):
        if False:
            yield
        return self._value


def _async_return(value):
    return _AsyncReturn(value)


def _client(user: dict):
    app = FastAPI()
    app.include_router(kb_routes.router)
    app.include_router(folder_routes.router)
    app.include_router(doc_routes.router)
    purge = FakePurge()
    kbdb = FakeKbDb()

    # 删除任务模块桩（路由函数内 import，打模块属性即生效）
    import knowledge_mining.mining.kb.services.purge_tasks as pt
    pt.PurgeTaskRepo = FakeTaskRepo
    pt.start_purge_task = _fake_start_purge_task
    FakeTaskRepo.tasks = []
    app.state.pg_pool = None          # 路由仅经桩访问，占位即可

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


def test_kb_delete_schedules_background_task():
    """2026-09-16 二期：确认后秒级禁用+入队（202），不再同步跑管线."""
    c, purge, _ = _client(OWNER)
    resp = c.request("DELETE", "/api/kb/kb-1", json={"confirm_name": "交付局知识库"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["ok"] is True and body["task_id"] and body["status"] == "queued"
    assert purge.calls == []                         # 管线不在请求内跑
    assert FakeTaskRepo.tasks and FakeTaskRepo.tasks[0]["kb_id"] == "kb-1"

    # 幂等：同库再删 → 返回既有任务，不重复入队
    resp2 = c.request("DELETE", "/api/kb/kb-1", json={"confirm_name": "交付局知识库"})
    assert resp2.status_code == 202
    assert resp2.json()["already_started"] is True
    assert len(FakeTaskRepo.tasks) == 1


def test_kb_delete_accepts_soft_deleted_kb_for_cleanup():
    """软删态库可彻底删除（内网存量善后入口）——同走后台任务."""
    c, purge, _ = _client(OWNER)
    resp = c.request("DELETE", "/api/kb/kb-old", json={"confirm_name": "旧公共库"})
    assert resp.status_code == 202
    assert FakeTaskRepo.tasks and FakeTaskRepo.tasks[0]["kb_id"] == "kb-old"


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


def test_list_deleted_kbs_domain_admin_scoped():
    """域管理员可见本域已删库；同一人查其他域（d2）仍 403（跨域拒绝）。"""
    c, _, kbdb = _client(DOMAIN_ADMIN)
    resp = c.get("/api/kb", params={"domain": "d1", "include_deleted": True})
    assert resp.status_code == 200
    assert kbdb.deleted_listed
    assert c.get("/api/kb", params={"domain": "d2", "include_deleted": True}).status_code == 403


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
    """在途挖掘 Run 门禁（审查 M-2）：kb_busy → 409，不删分毫、不入队."""
    c, purge, _ = _client(OWNER)
    purge.busy = True
    resp = c.request("DELETE", "/api/kb/kb-1", json={"confirm_name": "交付局知识库"})
    assert resp.status_code == 409
    assert "kb_busy" in resp.json()["detail"]
    assert purge.calls == []
    assert FakeTaskRepo.tasks == []
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


def test_purge_tasks_endpoint_visibility():
    """进度端点：admin 看全域；普通成员只看自己发起的."""
    # 先建全部 client（_client 会清空 tasks），再注入数据
    c_admin, _, _ = _client(ADMIN)
    c_owner, _, _ = _client(OWNER)
    c_viewer, _, _ = _client(VIEWER)
    t1 = {"id": "t1", "kb_id": "kb-1", "kb_name": "A", "domain": "d1",
          "status": "running", "phase": "snapshots",
          "progress_json": {"snapshots_total": 10, "snapshots_reclaimed": 3},
          "requested_by": "u-owner", "error": None,
          "created_at": "t", "updated_at": "t"}
    t2 = {"id": "t2", "kb_id": "kb-2", "kb_name": "B", "domain": "d1",
          "status": "done", "phase": "finalize", "progress_json": {},
          "requested_by": "u-other", "error": None,
          "created_at": "t", "updated_at": "t"}

    FakeTaskRepo.tasks = [t1, t2]
    resp = c_admin.get("/api/kb/purge-tasks", params={"domain": "d1"})
    assert resp.status_code == 200
    assert len(resp.json()["tasks"]) == 2            # admin 全量

    FakeTaskRepo.tasks = [t1]
    resp = c_owner.get("/api/kb/purge-tasks", params={"domain": "d1"})
    assert [t["id"] for t in resp.json()["tasks"]] == ["t1"]

    resp = c_viewer.get("/api/kb/purge-tasks", params={"domain": "d1"})
    assert resp.json()["tasks"] == []                # 非发起人看不到
