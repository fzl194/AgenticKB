"""P3 — /api/kb/{kb_id}/documents routes (upload/zip/list/get/patch/download/permissions)."""
from __future__ import annotations

import io
import os
import zipfile

import pytest
from openpyxl import Workbook
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.routes.documents import router as docs_router
from knowledge_mining.mining.kb.routes.kbs import router as kb_router
from knowledge_mining.tests.conftest import kb_headers

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
        h = kb_headers("alice")
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
        h = kb_headers("alice")
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


async def test_upload_zip_with_nested_xlsx_is_discoverable(async_pool, upload_root):
    xlsx = io.BytesIO()
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["name", "status"])
    worksheet.append(["AMF", "active"])
    workbook.save(xlsx)

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nested/inventory.xlsx", xlsx.getvalue())

    async with await _client(async_pool) as client:
        headers = {"X-KB-User": "alice"}
        kb_id = (
            await client.post(
                "/api/kb",
                json={"domain": DOMAIN, "name": "KB Excel ZIP"},
                headers=headers,
            )
        ).json()["id"]
        response = await client.post(
            f"/api/kb/{kb_id}/documents",
            files={"file": ("excel.zip", archive.getvalue())},
            headers=headers,
        )

    assert response.status_code == 201, response.text
    documents = response.json()["documents"]
    assert [document["document_name"] for document in documents] == [
        "inventory.xlsx"
    ]
    assert documents[0]["document_key"] == "doc:/nested/inventory.xlsx"

    from knowledge_mining.mining.ingestion import ingest_directory

    ingested, _ = ingest_directory(upload_root / kb_id)
    assert [document.relative_path for document in ingested] == [
        "nested/inventory.xlsx"
    ]


async def test_other_user_cannot_access_private_kb_docs(async_pool, upload_root):
    async with await _client(async_pool) as c:
        h_a, h_b = kb_headers("alice"), kb_headers("bob")
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
        h = kb_headers("alice")
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
        h = kb_headers("alice")
        kb_id = (await c.post("/api/kb", json={"domain": DOMAIN, "name": "KBtr"}, headers=h)).json()["id"]
        r = await c.post(
            f"/api/kb/{kb_id}/documents",
            files={"file": ("a.txt", b"x")},
            data={"directory": "../escape"},
            headers=h,
        )
        assert r.status_code == 400


async def test_non_member_upload_to_private_kb_rejected(async_pool, upload_root):
    """C3 IDOR 回归：非成员向他人 private KB 上传 → 404（不可见，不泄露存在性）。

    修复前 upload/upload_zip 入口无 _assert_write，任何 member 猜到 kb_id 即可塞文件。
    """
    async with await _client(async_pool) as c:
        h_a, h_b = kb_headers("alice"), kb_headers("bob")
        kb_id = (await c.post(
            "/api/kb", json={"domain": DOMAIN, "name": "priv-up", "visibility": "private"}, headers=h_a,
        )).json()["id"]
        r = await c.post(
            f"/api/kb/{kb_id}/documents", files={"file": ("x.txt", b"x")}, headers=h_b,
        )
        assert r.status_code == 404, r.text


async def test_viewer_upload_forbidden_403(async_pool, upload_root):
    """C3 回归：viewer 能读不能写 → 上传 403。

    用 private 库(public 库下 viewer 成员已被新语义拒绝——public 全员可读,viewer 冗余)。
    """
    async with await _client(async_pool) as c:
        h_a, h_b = kb_headers("alice"), kb_headers("bob")
        kb_id = (await c.post(
            "/api/kb", json={"domain": DOMAIN, "name": "priv-view", "visibility": "private"}, headers=h_a,
        )).json()["id"]
        await c.get(f"/api/kb?domain={DOMAIN}", headers=h_b)  # 让 bob upsert 进 kb_users
        r = await c.post(
            f"/api/kb/{kb_id}/members", json={"username": "bob", "role": "viewer"}, headers=h_a,
        )
        assert r.status_code == 201, r.text
        r = await c.post(
            f"/api/kb/{kb_id}/documents", files={"file": ("x.txt", b"x")}, headers=h_b,
        )
        assert r.status_code == 403, r.text


async def test_editor_and_owner_can_upload_201(async_pool, upload_root):
    """C3 回归：editor 能写 → 201；owner 自然也 201。"""
    async with await _client(async_pool) as c:
        h_a, h_b = kb_headers("alice"), kb_headers("bob")
        kb_id = (await c.post(
            "/api/kb", json={"domain": DOMAIN, "name": "edit-up", "visibility": "public"}, headers=h_a,
        )).json()["id"]
        await c.get(f"/api/kb?domain={DOMAIN}", headers=h_b)
        await c.post(
            f"/api/kb/{kb_id}/members", json={"username": "bob", "role": "editor"}, headers=h_a,
        )
        r = await c.post(
            f"/api/kb/{kb_id}/documents", files={"file": ("bob.txt", b"b")}, headers=h_b,
        )
        assert r.status_code == 201, r.text
        r = await c.post(
            f"/api/kb/{kb_id}/documents", files={"file": ("alice.txt", b"a")}, headers=h_a,
        )
        assert r.status_code == 201, r.text
