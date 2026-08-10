"""B5 — /api/kb/admin/reload-auth-config 内部端点（mining auth 配置热重载）。

main_control 的 reload-auth 改 auth.yaml 后扇出调本端点；mining 强制重拉控制面
auth.yaml 刷 internal_verify_secret 缓存，避免网关注入新 secret、mining 仍验旧值 → 全代理 401。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.routes.auth import router as auth_router
from knowledge_mining.tests.conftest import kb_headers

pytestmark = pytest.mark.asyncio


async def _client():
    app = FastAPI()
    app.include_router(auth_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_reload_auth_config_requires_internal_auth():
    """无 X-Internal-Auth / 错 secret → 401（_require_internal 守卫）。"""
    async with await _client() as c:
        assert (await c.post("/api/kb/admin/reload-auth-config")).status_code == 401
        r = await c.post("/api/kb/admin/reload-auth-config", headers={"X-Internal-Auth": "wrong"})
        assert r.status_code == 401


async def test_reload_auth_config_calls_fetch_force(monkeypatch):
    """带正确 X-Internal-Auth → 200，且以 force=True 重拉控制面 auth 配置。"""
    called = {"force": None}
    from knowledge_mining.mining.infra import control_plane

    def _stub(force: bool = False):
        called["force"] = force
        control_plane.set_auth_config({"internal_verify_secret": "test-ivs"})
        return {"internal_verify_secret": "test-ivs"}

    monkeypatch.setattr(control_plane, "fetch_auth_config", _stub)
    async with await _client() as c:
        r = await c.post("/api/kb/admin/reload-auth-config", headers=kb_headers("admin"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["internal_verify_secret_present"] is True
        assert called["force"] is True  # 关键：用 force=True 强制重拉，非走缓存
