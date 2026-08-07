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


async def test_private_member_read_but_not_write(async_pool):
    async with await _client(async_pool) as c:
        h_alice, h_bob = kb_headers("alice"), kb_headers("bob")
        r = await c.post("/api/kb", json={"domain": DOMAIN, "name": "sh", "visibility": "private"}, headers=h_alice)
        kb_id = r.json()["id"]

        # bob 未入成员 → 看不到 private
        r = await c.get(f"/api/kb?domain={DOMAIN}", headers=h_bob)
        assert all(k["id"] != kb_id for k in r.json())

        # alice 加 bob 为 viewer(bob 已被 current_user 建行)
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
        # carol 必须已存在(admin 创建或登录过);否则新护栏拒绝陌生用户(404)。
        await c.get(f"/api/kb?domain={DOMAIN}", headers=kb_headers("carol"))
        r = await c.post(f"/api/kb/{kb_id}/members", json={"username": "carol"}, headers=h_bob)
        assert r.status_code == 201


async def test_invalid_visibility_rejected(async_pool):
    """visibility 收口为 private/public:shared 及其它非法值 → 400(应用层校验,非 500)。"""
    async with await _client(async_pool) as c:
        h = kb_headers("alice")
        for vis in ("shared", "weird", "PROTECTED"):
            r = await c.post(
                "/api/kb", json={"domain": DOMAIN, "name": f"bad-{vis}", "visibility": vis}, headers=h,
            )
            assert r.status_code == 400, f"{vis}: {r.text}"


async def test_add_member_rejects_unknown_user(async_pool):
    """加成员不再 upsert 自动建用户:陌生 username → 404;已存在用户 → 201。"""
    async with await _client(async_pool) as c:
        h_alice = kb_headers("alice")
        r = await c.post("/api/kb", json={"domain": DOMAIN, "name": "kb-m"}, headers=h_alice)
        kb_id = r.json()["id"]
        # ghost 从未登录/未创建 → 加成员 404(不再静默注册)
        r = await c.post(
            f"/api/kb/{kb_id}/members", json={"username": "ghost", "role": "editor"}, headers=h_alice,
        )
        assert r.status_code == 404, r.text
        # bob 先触发 current_user 建行 → 再加成功
        await c.get(f"/api/kb?domain={DOMAIN}", headers=kb_headers("bob"))
        r = await c.post(
            f"/api/kb/{kb_id}/members", json={"username": "bob", "role": "editor"}, headers=h_alice,
        )
        assert r.status_code == 201, r.text


async def test_public_kb_rejects_viewer_member(async_pool):
    """public 库全员可读,viewer 成员冗余 → 400;editor 仍可加(写权限有意义)。"""
    async with await _client(async_pool) as c:
        h_alice = kb_headers("alice")
        r = await c.post("/api/kb", json={"domain": DOMAIN, "name": "pub", "visibility": "public"}, headers=h_alice)
        kb_id = r.json()["id"]
        await c.get(f"/api/kb?domain={DOMAIN}", headers=kb_headers("bob"))
        # public + viewer → 400
        r = await c.post(
            f"/api/kb/{kb_id}/members", json={"username": "bob", "role": "viewer"}, headers=h_alice,
        )
        assert r.status_code == 400, r.text
        # public + editor → 201
        r = await c.post(
            f"/api/kb/{kb_id}/members", json={"username": "bob", "role": "editor"}, headers=h_alice,
        )
        assert r.status_code == 201, r.text


async def test_list_member_candidates(async_pool):
    """候选用户接口:排除 owner 自己 + 已是成员;最小字段集;非成员对 private 库 → 404;viewer → 403。"""
    async with await _client(async_pool) as c:
        h_alice = kb_headers("alice")
        r = await c.post("/api/kb", json={"domain": DOMAIN, "name": "cand"}, headers=h_alice)
        kb_id = r.json()["id"]
        # 预置候选用户:bob / carol / dave(触发 current_user 建行)
        for name in ("bob", "carol", "dave"):
            await c.get(f"/api/kb?domain={DOMAIN}", headers=kb_headers(name))
        # alice 加 bob 为成员
        await c.post(f"/api/kb/{kb_id}/members", json={"username": "bob", "role": "editor"}, headers=h_alice)
        # 候选:含 carol/dave;绝不含 alice(owner)与 bob(已成员)
        r = await c.get(f"/api/kb/{kb_id}/members/candidates", headers=h_alice)
        assert r.status_code == 200, r.text
        rows = r.json()
        names = {u["username"] for u in rows}
        assert "carol" in names and "dave" in names
        assert "alice" not in names  # owner 排除
        assert "bob" not in names     # 已成员排除
        # 最小字段集:不泄露 site_role/status/has_password
        assert set(rows[0].keys()) == {"id", "username", "display_name"}
        # 非成员对 private 库 → 404(不泄露存在性)
        r = await c.get(f"/api/kb/{kb_id}/members/candidates", headers=kb_headers("eve"))
        assert r.status_code == 404
        # viewer 成员可见但不能写 → 候选接口要求写权限 → 403
        await c.post(f"/api/kb/{kb_id}/members", json={"username": "carol", "role": "viewer"}, headers=h_alice)
        r = await c.get(f"/api/kb/{kb_id}/members/candidates", headers=kb_headers("carol"))
        assert r.status_code == 403


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
