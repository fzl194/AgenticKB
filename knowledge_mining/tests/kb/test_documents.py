"""P3 — /api/kb/{kb_id}/documents routes (upload/zip/list/get/patch/download/permissions)."""
from __future__ import annotations

import io
import os
import zipfile

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.routes.documents import router as docs_router
from knowledge_mining.mining.kb.routes.kbs import router as kb_router

pytestmark = pytest.mark.asyncio
DOMAIN = "cloud_core_network"


@pytest.fixture(scope="module")
def upload_root(tmp_path_factory):
    """把上传根指到 tmp 目录（覆盖控制面服务配置缓存的 upload.root）。"""
    from knowledge_mining.mining.infra.control_plane import override_upload_root
    p = tmp_path_factory.mktemp("kb_uploads")
    override_upload_root(str(p))
    yield p


async def _client(async_pool):
    app = FastAPI()
    app.state.pg_pool = async_pool
    app.include_router(kb_router)
    app.include_router(docs_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_upload_list_get_patch_download(async_pool, upload_root):
    async with await _client(async_pool) as c:
        h = {"X-KB-User": "alice"}
        kb_id = (await c.post("/api/kb", json={"domain": DOMAIN, "name": "KB1"}, headers=h)).json()["id"]

        r = await c.post(
            f"/api/kb/{kb_id}/documents",
            files={"file": ("a.txt", b"hello world")},
            data={"directory": "sub"},
            headers=h,
        )
        assert r.status_code == 201, r.text
        doc = r.json()
        doc_id = doc["id"]
        assert doc["status"] == "uploaded"
        assert doc["document_key"] == "doc:/sub/a.txt"
        assert doc["directory_path"] == "sub"

        r = await c.get(f"/api/kb/{kb_id}/documents", headers=h)
        assert r.status_code == 200 and len(r.json()) == 1

        r = await c.get(f"/api/kb/{kb_id}/documents/{doc_id}", headers=h)
        assert r.json()["status"] == "uploaded"

        r = await c.patch(
            f"/api/kb/{kb_id}/documents/{doc_id}",
            json={"document_type": "reference", "document_name": "renamed.txt"},
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["document_type"] == "reference"

        r = await c.get(f"/api/kb/{kb_id}/documents/{doc_id}/download", headers=h)
        assert r.status_code == 200 and r.content == b"hello world"


async def test_upload_zip_extracts_with_directory(async_pool, upload_root):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dir1/x.txt", "x contents")
        zf.writestr("dir1/y.txt", "y contents")
    async with await _client(async_pool) as c:
        h = {"X-KB-User": "alice"}
        kb_id = (await c.post("/api/kb", json={"domain": DOMAIN, "name": "KBz"}, headers=h)).json()["id"]
        r = await c.post(
            f"/api/kb/{kb_id}/documents",
            files={"file": ("u.zip", buf.getvalue())},
            headers=h,
        )
        assert r.status_code == 201, r.text
        docs = r.json()["documents"]
        assert {d["document_name"] for d in docs} == {"x.txt", "y.txt"}
        assert {d["directory_path"] for d in docs} == {"dir1"}
        assert all(d["document_key"].startswith("doc:/dir1/") for d in docs)


async def test_other_user_cannot_access_private_kb_docs(async_pool, upload_root):
    async with await _client(async_pool) as c:
        h_a, h_b = {"X-KB-User": "alice"}, {"X-KB-User": "bob"}
        kb_id = (await c.post("/api/kb", json={"domain": DOMAIN, "name": "priv"}, headers=h_a)).json()["id"]
        doc_id = (await c.post(
            f"/api/kb/{kb_id}/documents", files={"file": ("a.txt", b"x")}, headers=h_a,
        )).json()["id"]

        # bob 对 private KB 的文档：list/get/download 全 404（不泄露）
        assert (await c.get(f"/api/kb/{kb_id}/documents", headers=h_b)).status_code == 404
        assert (await c.get(f"/api/kb/{kb_id}/documents/{doc_id}", headers=h_b)).status_code == 404
        assert (await c.get(f"/api/kb/{kb_id}/documents/{doc_id}/download", headers=h_b)).status_code == 404
        assert (await c.patch(f"/api/kb/{kb_id}/documents/{doc_id}", json={"document_name": "h"}, headers=h_b)).status_code == 404


async def test_delete_removes_file_and_identity(async_pool, upload_root):
    """DELETE 真删除：磁盘文件 + 身份行一并移除（撤回是另一个概念，待 release 机制）。"""
    async with await _client(async_pool) as c:
        h = {"X-KB-User": "alice"}
        kb_id = (await c.post("/api/kb", json={"domain": DOMAIN, "name": "KBdel"}, headers=h)).json()["id"]
        doc_id = (await c.post(
            f"/api/kb/{kb_id}/documents", files={"file": ("a.txt", b"x")}, headers=h,
        )).json()["id"]
        p = upload_root / kb_id / "a.txt"
        assert p.is_file()  # 上传后磁盘有文件
        r = await c.delete(f"/api/kb/{kb_id}/documents/{doc_id}", headers=h)
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert not p.exists()  # 磁盘文件已删
        # 身份行已删：再 get → 404
        assert (await c.get(f"/api/kb/{kb_id}/documents/{doc_id}", headers=h)).status_code == 404


async def test_path_traversal_rejected(async_pool, upload_root):
    async with await _client(async_pool) as c:
        h = {"X-KB-User": "alice"}
        kb_id = (await c.post("/api/kb", json={"domain": DOMAIN, "name": "KBtr"}, headers=h)).json()["id"]
        r = await c.post(
            f"/api/kb/{kb_id}/documents",
            files={"file": ("a.txt", b"x")},
            data={"directory": "../escape"},
            headers=h,
        )
        assert r.status_code == 400
