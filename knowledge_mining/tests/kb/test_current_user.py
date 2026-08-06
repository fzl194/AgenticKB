"""current_user（X-KB-User + X-Internal-Auth 双校验）+ require_admin 测试。"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.infra import control_plane
from knowledge_mining.mining.kb.auth import current_user, require_admin
from knowledge_mining.mining.kb.db import KbDB


@pytest.fixture(autouse=True)
def _set_secret():
    control_plane.set_auth_config({"internal_verify_secret": "ivs-test"})
    yield
    control_plane.set_auth_config({})


async def _client(pg_pool):
    app = FastAPI()
    app.state.pg_pool = pg_pool

    @app.get("/who")
    async def who(user=Depends(current_user)):
        return user

    @app.get("/admin")
    async def admin(user=Depends(require_admin)):
        return user

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_missing_xkbuser_header_401(async_pool):
    async with await _client(async_pool) as c:
        assert (await c.get("/who")).status_code == 401


@pytest.mark.asyncio
async def test_missing_internal_auth_401(async_pool):
    """核心安全断言：有 X-KB-User 但缺 X-Internal-Auth（直连伪造）→ 401。"""
    async with await _client(async_pool) as c:
        r = await c.get("/who", headers={"X-KB-User": "alice"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_wrong_internal_auth_401(async_pool):
    async with await _client(async_pool) as c:
        r = await c.get("/who", headers={"X-KB-User": "alice", "X-Internal-Auth": "wrong"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_current_user_ok_upserts_member(async_pool):
    async with await _client(async_pool) as c:
        r = await c.get("/who", headers={"X-KB-User": "alice", "X-Internal-Auth": "ivs-test"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["username"] == "alice"
        assert body["site_role"] == "member"


@pytest.mark.asyncio
async def test_require_admin_member_403(async_pool):
    async with await _client(async_pool) as c:
        h = {"X-KB-User": "alice", "X-Internal-Auth": "ivs-test"}
        await c.get("/who", headers=h)  # upsert alice as member
        assert (await c.get("/admin", headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_require_admin_admin_ok(async_pool):
    db = KbDB(async_pool)
    await db.create_user(username="root", password_hash="h", site_role="admin")
    async with await _client(async_pool) as c:
        r = await c.get("/admin", headers={"X-KB-User": "root", "X-Internal-Auth": "ivs-test"})
        assert r.status_code == 200
        assert r.json()["site_role"] == "admin"


@pytest.mark.asyncio
async def test_secret_not_initialized_401(async_pool, monkeypatch):
    """auth.yaml 未就绪（get_internal_verify_secret 返回 None）→ 一律 401。"""
    control_plane.set_auth_config({})  # 无 secret
    async with await _client(async_pool) as c:
        r = await c.get("/who", headers={"X-KB-User": "alice", "X-Internal-Auth": "ivs-test"})
        assert r.status_code == 401
