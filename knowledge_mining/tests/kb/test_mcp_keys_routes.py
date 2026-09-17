"""51号批次2（Task 4）：/users/me/mcp-keys 路由族（ASGITransport + PG 真库）。"""
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.routes.mcp_keys import router as mcp_keys_router
from knowledge_mining.mining.kb.services.mcp_key_service import (
    KEY_PREFIX_TAG,
    McpKeyService,
)
from knowledge_mining.tests.conftest import kb_headers

BASE = "/api/kb/users/me/mcp-keys"


@pytest.fixture
def kbdb(async_pool):
    return KbDB(async_pool)


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


async def _client(async_pool):
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig

    app = FastAPI()
    app.state.pg_pool = async_pool
    app.state.db_config = MiningDbConfig()
    app.include_router(mcp_keys_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _mk_user(async_pool, *, site_role: str = "member") -> dict:
    db = KbDB(async_pool)
    return await db.create_user(
        username=f"mkr_{site_role}_{_suffix()}", site_role=site_role,
    )


async def _create(c, name: str, domain: str, headers) -> dict:
    r = await c.post(BASE, json={"name": name, "domain": domain},
                     headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_create_and_list_keys(async_pool, kbdb):
    s = _suffix()
    u = await _mk_user(async_pool)
    await kbdb.set_user_domains(user_id=u["id"], domains=["generic"])
    async with await _client(async_pool) as c:
        headers = kb_headers(u["username"])
        created = await _create(c, f"我的钥匙-{s}", "generic", headers)
        assert created["key"].startswith(KEY_PREFIX_TAG)
        assert created["key_prefix"]
        assert created["status"] == "active"
        assert created["domain"] == "generic"
        # 列表可见（无明文）
        r = await c.get(BASE, headers=headers)
        assert r.status_code == 200, r.text
        keys = r.json()["keys"]
        mine = [k for k in keys if k["id"] == created["id"]]
        assert len(mine) == 1
        assert "key" not in mine[0]
        assert mine[0]["domain_bound"] is True


@pytest.mark.asyncio
async def test_create_unbound_domain_403(async_pool, kbdb):
    s = _suffix()
    u = await _mk_user(async_pool)
    await kbdb.set_user_domains(user_id=u["id"], domains=["generic"])
    async with await _client(async_pool) as c:
        r = await c.post(BASE, json={"name": f"k-{s}", "domain": "odn"},
                         headers=kb_headers(u["username"]))
        assert r.status_code == 403, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "domain_not_bound"
        assert "odn" in detail["message"]


@pytest.mark.asyncio
async def test_create_bad_domain_422(async_pool):
    s = _suffix()
    u = await _mk_user(async_pool)
    async with await _client(async_pool) as c:
        r = await c.post(BASE, json={"name": f"k-{s}", "domain": "no_such_domain"},
                         headers=kb_headers(u["username"]))
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_create_name_conflict_and_limit_409(async_pool, kbdb):
    s = _suffix()
    u = await _mk_user(async_pool)
    await kbdb.set_user_domains(user_id=u["id"], domains=["generic"])
    async with await _client(async_pool) as c:
        headers = kb_headers(u["username"])
        await _create(c, f"dup-{s}", "generic", headers)
        r = await c.post(BASE, json={"name": f"dup-{s}", "domain": "generic"},
                         headers=headers)
        assert r.status_code == 409, r.text
        # 上限：再造 9 把（共 10）→ 第 11 把 409
        for i in range(9):
            await _create(c, f"k{i}-{s}", "generic", headers)
        r = await c.post(BASE, json={"name": f"overflow-{s}", "domain": "generic"},
                         headers=headers)
        assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_rotate_invalidates_old_plaintext(async_pool, kbdb):
    s = _suffix()
    u = await _mk_user(async_pool)
    await kbdb.set_user_domains(user_id=u["id"], domains=["generic"])
    svc = McpKeyService(kbdb)
    async with await _client(async_pool) as c:
        headers = kb_headers(u["username"])
        created = await _create(c, f"rot-{s}", "generic", headers)
        r = await c.post(f"{BASE}/{created['id']}/rotate", headers=headers)
        assert r.status_code == 200, r.text
        rotated = r.json()
        assert rotated["key"].startswith(KEY_PREFIX_TAG)
        assert rotated["key_prefix"] and rotated["rotated_at"]
        assert await svc.verify_key(created["key"]) is None  # 旧明文失效
        assert (await svc.verify_key(rotated["key"]))["key_id"] == created["id"]


@pytest.mark.asyncio
async def test_revoke_then_config_409(async_pool, kbdb):
    s = _suffix()
    u = await _mk_user(async_pool)
    await kbdb.set_user_domains(user_id=u["id"], domains=["generic"])
    async with await _client(async_pool) as c:
        headers = kb_headers(u["username"])
        created = await _create(c, f"rev-{s}", "generic", headers)
        r = await c.post(f"{BASE}/{created['id']}/revoke", headers=headers)
        assert r.status_code == 204
        r = await c.put(f"{BASE}/{created['id']}/config",
                        json={"instructions": "x"}, headers=headers)
        assert r.status_code == 409, r.text
        # 幂等：重复 revoke 仍 204
        r = await c.post(f"{BASE}/{created['id']}/revoke", headers=headers)
        assert r.status_code == 204


@pytest.mark.asyncio
async def test_put_open_kbs_filters_cross_domain(async_pool, kbdb):
    s = _suffix()
    u = await _mk_user(async_pool)
    await kbdb.set_user_domains(user_id=u["id"], domains=["generic", "odn"])
    kb_g = await kbdb.create_kb(domain="generic", name=f"mkr-g-{s}", owner_id=u["id"])
    kb_o = await kbdb.create_kb(domain="odn", name=f"mkr-o-{s}", owner_id=u["id"])
    async with await _client(async_pool) as c:
        headers = kb_headers(u["username"])
        created = await _create(c, f"okb-{s}", "generic", headers)
        r = await c.put(f"{BASE}/{created['id']}/open-kbs",
                        json={"kb_ids": [kb_g["id"], kb_o["id"]]}, headers=headers)
        assert r.status_code == 200, r.text
        # 越域 kb_o（generic 钥匙收不到）不在返回里
        assert r.json()["open_kb_ids"] == [kb_g["id"]]


@pytest.mark.asyncio
async def test_list_domain_bound_flag_and_admin(async_pool, kbdb):
    s = _suffix()
    u = await _mk_user(async_pool)
    admin = await _mk_user(async_pool, site_role="admin")
    await kbdb.set_user_domains(user_id=u["id"], domains=["generic", "odn"])
    async with await _client(async_pool) as c:
        h = kb_headers(u["username"])
        k_g = await _create(c, f"g-{s}", "generic", h)
        await _create(c, f"o-{s}", "odn", h)
        # 解绑 odn → 该域钥匙 domain_bound=False
        await kbdb.set_user_domains(user_id=u["id"], domains=["generic"])
        r = await c.get(BASE, headers=h)
        by_dom = {k["domain"]: k for k in r.json()["keys"]}
        assert by_dom["generic"]["domain_bound"] is True
        assert by_dom["odn"]["domain_bound"] is False
        # admin 视角：本人无钥匙但 is_admin 语义由自身钥匙验证——admin 无绑定仍可建
        created = await _create(c, f"a-{s}", "odn", kb_headers(admin["username"]))
        assert created["domain"] == "odn"
        r = await c.get(BASE, headers=kb_headers(admin["username"]))
        assert [k["domain_bound"] for k in r.json()["keys"]] == [True]


@pytest.mark.asyncio
async def test_other_users_key_404(async_pool, kbdb):
    s = _suffix()
    u1 = await _mk_user(async_pool)
    u2 = await _mk_user(async_pool)
    await kbdb.set_user_domains(user_id=u1["id"], domains=["generic"])
    async with await _client(async_pool) as c:
        created = await _create(c, f"mine-{s}", "generic", kb_headers(u1["username"]))
        r = await c.post(f"{BASE}/{created['id']}/rotate",
                         headers=kb_headers(u2["username"]))
        assert r.status_code == 404
        assert r.json()["detail"] == "钥匙不存在"


@pytest.mark.asyncio
async def test_unauthenticated_401(async_pool):
    async with await _client(async_pool) as c:
        r = await c.get(BASE)
        assert r.status_code == 401
