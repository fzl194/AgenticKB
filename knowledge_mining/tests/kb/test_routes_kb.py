"""P2.4 — /api/kb routes end-to-end (httpx ASGI transport)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.routes.kbs import router as kb_router
from knowledge_mining.tests.conftest import kb_headers

pytestmark = pytest.mark.asyncio
DOMAIN = "cloud_core_network"


async def _client(async_pool):
    app = FastAPI()
    app.state.pg_pool = async_pool
    app.include_router(kb_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_auth_missing_header_rejected(async_pool):
    async with await _client(async_pool) as c:
        r = await c.get(f"/api/kb?domain={DOMAIN}")
        assert r.status_code == 401


async def test_create_list_get_update_delete(async_pool):
    async with await _client(async_pool) as c:
        h = kb_headers("alice")
        r = await c.post("/api/kb", json={"domain": DOMAIN, "name": "KB-A", "visibility": "private"}, headers=h)
        assert r.status_code == 201, r.text
        kb_id = r.json()["id"]

        r = await c.get(f"/api/kb?domain={DOMAIN}", headers=h)
        assert r.status_code == 200
        assert any(k["id"] == kb_id for k in r.json())

        r = await c.get(f"/api/kb/{kb_id}", headers=h)
        assert r.status_code == 200

        r = await c.patch(f"/api/kb/{kb_id}", json={"name": "KB-A2"}, headers=h)
        assert r.status_code == 200 and r.json()["name"] == "KB-A2"

        r = await c.delete(f"/api/kb/{kb_id}", headers=h)
        assert r.status_code == 200
        r = await c.get(f"/api/kb/{kb_id}", headers=h)
        assert r.status_code == 404  # 软删 → NotFound，不泄露存在性


async def test_other_user_cannot_see_private(async_pool):
    async with await _client(async_pool) as c:
        h_alice, h_bob = kb_headers("alice"), kb_headers("bob")
        r = await c.post("/api/kb", json={"domain": DOMAIN, "name": "priv", "visibility": "private"}, headers=h_alice)
        kb_id = r.json()["id"]

        r = await c.get(f"/api/kb?domain={DOMAIN}", headers=h_bob)
        assert all(k["id"] != kb_id for k in r.json())
        # get/patch 对无权 KB → 404（NotFound，不泄露）
        assert (await c.get(f"/api/kb/{kb_id}", headers=h_bob)).status_code == 404
        assert (await c.patch(f"/api/kb/{kb_id}", json={"name": "hack"}, headers=h_bob)).status_code == 404


async def test_shared_member_read_but_not_write(async_pool):
    async with await _client(async_pool) as c:
        h_alice, h_bob = kb_headers("alice"), kb_headers("bob")
        r = await c.post("/api/kb", json={"domain": DOMAIN, "name": "sh", "visibility": "shared"}, headers=h_alice)
        kb_id = r.json()["id"]

        # bob 未入成员 → 看不到 shared
        r = await c.get(f"/api/kb?domain={DOMAIN}", headers=h_bob)
        assert all(k["id"] != kb_id for k in r.json())

        # alice 加 bob 为 viewer
        r = await c.post(f"/api/kb/{kb_id}/members", json={"username": "bob", "role": "viewer"}, headers=h_alice)
        assert r.status_code == 201, r.text

        # bob 现在看得到，能读成员列表
        r = await c.get(f"/api/kb?domain={DOMAIN}", headers=h_bob)
        assert any(k["id"] == kb_id for k in r.json())
        assert (await c.get(f"/api/kb/{kb_id}/members", headers=h_bob)).status_code == 200

        # bob 是 viewer 不能写（加成员）→ 403
        r = await c.post(f"/api/kb/{kb_id}/members", json={"username": "carol"}, headers=h_bob)
        assert r.status_code == 403

        # alice 升级 bob 为 editor → bob 能加成员
        await c.post(f"/api/kb/{kb_id}/members", json={"username": "bob", "role": "editor"}, headers=h_alice)
        r = await c.post(f"/api/kb/{kb_id}/members", json={"username": "carol"}, headers=h_bob)
        assert r.status_code == 201


async def test_invalid_domain_rejected(async_pool):
    async with await _client(async_pool) as c:
        r = await c.post("/api/kb", json={"domain": "no-such-domain", "name": "X"}, headers=kb_headers("alice"))
        assert r.status_code == 400


async def test_duplicate_name_conflict(async_pool):
    async with await _client(async_pool) as c:
        h = kb_headers("alice")
        await c.post("/api/kb", json={"domain": DOMAIN, "name": "dup"}, headers=h)
        r = await c.post("/api/kb", json={"domain": DOMAIN, "name": "dup"}, headers=h)
        assert r.status_code == 409
        # 不同 domain 同名允许
        r = await c.post("/api/kb", json={"domain": "generic", "name": "dup"}, headers=h)
        assert r.status_code == 201
