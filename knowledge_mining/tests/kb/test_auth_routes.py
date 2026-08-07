"""/api/kb/auth/verify + /api/kb/users* 路由测试。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.routes.auth import router as auth_router
from knowledge_mining.mining.kb.routes.kbs import router as kb_router
from knowledge_mining.tests.conftest import kb_headers


async def _client(async_pool):
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    app = FastAPI()
    app.state.pg_pool = async_pool
    app.state.db_config = MiningDbConfig()
    app.include_router(auth_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_admin(async_pool, username="root"):
    """直接经 db 建一个 admin（绕过端点的 require_admin，用于测试 setup）。"""
    db = KbDB(async_pool)
    return await db.create_user(username=username, password_hash="x", site_role="admin")


@pytest.mark.asyncio
async def test_verify_wrong_internal_secret_401(async_pool):
    async with await _client(async_pool) as c:
        r = await c.post("/api/kb/auth/verify", json={"username": "x", "password": "y"},
                         headers={"X-Internal-Auth": "wrong"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_verify_missing_internal_secret_401(async_pool):
    async with await _client(async_pool) as c:
        r = await c.post("/api/kb/auth/verify", json={"username": "x", "password": "y"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_verify_creates_then_authenticates(async_pool):
    db = KbDB(async_pool)
    await _make_admin(async_pool)
    async with await _client(async_pool) as c:
        # root 建 alice（member，带可登录密码）
        r = await c.post("/api/kb/users",
                         json={"username": "alice", "password": "alicepw12", "site_role": "member"},
                         headers=kb_headers("root"))
        assert r.status_code == 201, r.text
        # verify alice：kb_headers 提供 X-Internal-Auth（verify 不读 X-KB-User）
        r = await c.post("/api/kb/auth/verify",
                         json={"username": "alice", "password": "alicepw12"},
                         headers=kb_headers("ignored"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["user"]["username"] == "alice"
        assert body["user"]["site_role"] == "member"


@pytest.mark.asyncio
async def test_verify_bad_password_401(async_pool):
    db = KbDB(async_pool)
    from knowledge_mining.mining.kb.security import hash_password
    await db.create_user(username="alice", password_hash=hash_password("rightpw12"), site_role="member")
    async with await _client(async_pool) as c:
        r = await c.post("/api/kb/auth/verify",
                         json={"username": "alice", "password": "wrongpw12"},
                         headers=kb_headers("i"))
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_users_list_admin_only(async_pool):
    await _make_admin(async_pool)
    db = KbDB(async_pool)
    await db.create_user(username="alice", password_hash="x", site_role="member")
    async with await _client(async_pool) as c:
        assert (await c.get("/api/kb/users", headers=kb_headers("root"))).status_code == 200
        # member 不能列用户
        assert (await c.get("/api/kb/users", headers=kb_headers("alice"))).status_code == 403
        # 无 X-Internal-Auth（伪造）→ 401
        assert (await c.get("/api/kb/users", headers={"X-KB-User": "root"})).status_code == 401


@pytest.mark.asyncio
async def test_create_user_admin_only(async_pool):
    await _make_admin(async_pool)
    async with await _client(async_pool) as c:
        # member 建用户 → 403
        db = KbDB(async_pool)
        await db.create_user(username="alice", password_hash="x", site_role="member")
        r = await c.post("/api/kb/users",
                         json={"username": "new", "password": "pw123456", "site_role": "member"},
                         headers=kb_headers("alice"))
        assert r.status_code == 403
        # admin 建用户 → 201
        r = await c.post("/api/kb/users",
                         json={"username": "new", "password": "pw123456", "site_role": "member"},
                         headers=kb_headers("root"))
        assert r.status_code == 201


@pytest.mark.asyncio
async def test_reset_password_admin(async_pool):
    admin = await _make_admin(async_pool)
    db = KbDB(async_pool)
    from knowledge_mining.mining.kb.security import verify_password
    target = await db.create_user(username="alice", password_hash="old", site_role="member")
    async with await _client(async_pool) as c:
        r = await c.post(f"/api/kb/users/{target['id']}/reset-password",
                         json={"password": "newpw345"},
                         headers=kb_headers("root"))
        assert r.status_code == 200, r.text
    assert verify_password("newpw345", (await db.get_user(target["id"]))["password_hash"])


@pytest.mark.asyncio
async def test_change_my_password(async_pool):
    from knowledge_mining.mining.kb.security import hash_password, verify_password
    db = KbDB(async_pool)
    u = await db.create_user(username="alice", password_hash=hash_password("oldpw123"), site_role="member")
    async with await _client(async_pool) as c:
        # 旧密码错 → 400
        r = await c.post("/api/kb/users/me/password",
                         json={"old": "wrong", "new": "newpw345"},
                         headers=kb_headers("alice"))
        assert r.status_code == 400
        # 正确 → 200
        r = await c.post("/api/kb/users/me/password",
                         json={"old": "oldpw123", "new": "newpw345"},
                         headers=kb_headers("alice"))
        assert r.status_code == 200, r.text
    assert verify_password("newpw345", (await db.get_user(u["id"]))["password_hash"])


@pytest.mark.asyncio
async def test_users_route_not_shadowed_by_kb_id(async_pool):
    """回归：真实 app 同时挂 kb_router（GET /api/kb/{kb_id}）+ auth_router（GET /api/kb/users）。
    auth_router 必须先注册，否则 GET /api/kb/users 被当成 kb_id="users" → 404（点「系统设置」报错的根因）。"""
    await _make_admin(async_pool, "root")
    app = FastAPI()
    app.state.pg_pool = async_pool
    app.include_router(auth_router)   # 与 app.py 同序：auth 先
    app.include_router(kb_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/kb/users", headers=kb_headers("root"))
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)
