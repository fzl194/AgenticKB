"""51号批次2（Task 4）：/users/me/mcp-keys 路由族（ASGITransport + PG 真库）。"""
import os
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.routes.auth import router as auth_router
from knowledge_mining.mining.kb.routes.mcp_keys import router as mcp_keys_router
from knowledge_mining.mining.kb.services.mcp_key_service import (
    KEY_PREFIX_TAG,
    McpKeyService,
)
from knowledge_mining.tests.conftest import kb_headers

BASE = "/api/kb/users/me/mcp-keys"
VERIFY = "/api/kb/auth/mcp-key-verify"
INTERNAL = {"X-Internal-Auth": os.environ.get("KB_TEST_INTERNAL_AUTH", "test-ivs")}


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
    app.include_router(auth_router)
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


# --------------------------------------- 51号批次2（Task 5）：mcp-key-verify 钥匙级

async def _mk_key_with_kb(async_pool, kbdb, c, headers):
    """member（绑定 generic）+ 一库 + 一把 generic 钥匙（开该库）→ (user, kb, created)。"""
    s = _suffix()
    u = await _mk_user(async_pool)
    await kbdb.set_user_domains(user_id=u["id"], domains=["generic"])
    kb = await kbdb.create_kb(domain="generic", name=f"mkv-{s}", owner_id=u["id"])
    created = await _create(c, f"v-{s}", "generic", headers(u["username"]))
    r = await c.put(f"{BASE}/{created['id']}/open-kbs",
                    json={"kb_ids": [kb["id"]]}, headers=headers(u["username"]))
    assert r.status_code == 200, r.text
    return u, kb, created


@pytest.mark.asyncio
async def test_verify_endpoint_ok_shape(async_pool, kbdb):
    async with await _client(async_pool) as c:
        u, kb, created = await _mk_key_with_kb(async_pool, kbdb, c, kb_headers)
        r = await c.post(VERIFY, json={"key": created["key"]}, headers=INTERNAL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["username"] == u["username"]
        assert body["user_id"] == u["id"]
        assert body["key_id"] == created["id"]
        assert body["key_domain"] == "generic"
        assert body["open_kb_ids"] == [kb["id"]]
        assert [k["id"] for k in body["open_kbs"]] == [kb["id"]]
        # N3：单域钥匙定死域——domains 字段明确删除
        assert "domains" not in body


@pytest.mark.asyncio
async def test_verify_unbound_domain_403(async_pool, kbdb):
    async with await _client(async_pool) as c:
        u, _kb, created = await _mk_key_with_kb(async_pool, kbdb, c, kb_headers)
        # 解绑所有域 → 同钥 403 domain_not_bound（N4 联动）
        await kbdb.set_user_domains(user_id=u["id"], domains=[])
        r = await c.post(VERIFY, json={"key": created["key"]}, headers=INTERNAL)
        assert r.status_code == 403, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "domain_not_bound"
        assert "已被解绑" in detail["message"]


@pytest.mark.asyncio
async def test_verify_revoked_and_wrong_key_401(async_pool, kbdb):
    async with await _client(async_pool) as c:
        _u, _kb, created = await _mk_key_with_kb(async_pool, kbdb, c, kb_headers)
        # 错误钥：与 miss 同文案（不区分）
        r = await c.post(VERIFY, json={"key": f"{KEY_PREFIX_TAG}nope"},
                         headers=INTERNAL)
        assert r.status_code == 401
        assert r.json()["detail"] == "invalid mcp key"
        # 吊销后同钥 → 401（同文案）
        r = await c.post(f"{BASE}/{created['id']}/revoke",
                         headers=kb_headers(_u["username"]))
        assert r.status_code == 204
        r = await c.post(VERIFY, json={"key": created["key"]}, headers=INTERNAL)
        assert r.status_code == 401
        assert r.json()["detail"] == "invalid mcp key"


@pytest.mark.asyncio
async def test_verify_normalizes_legacy_open_tools(async_pool, kbdb):
    """db 直写旧形状 open_tools → 验钥响应已归一（读时归一，不回写）。"""
    async with await _client(async_pool) as c:
        _u, _kb, created = await _mk_key_with_kb(async_pool, kbdb, c, kb_headers)
        async with async_pool.connection() as conn:
            await conn.execute(
                """UPDATE mcp_keys SET open_tools = %s::jsonb WHERE id = %s""",
                ('["search_knowledge", "get_evidence"]', created["id"]),
            )
        r = await c.post(VERIFY, json={"key": created["key"]}, headers=INTERNAL)
        assert r.status_code == 200, r.text
        # get_evidence（旧名）→ get_knowledge（_RENAMED_TOOLS 真实映射）
        assert r.json()["open_tools"] == ["search_knowledge", "get_knowledge"]
        # 只归一响应，不回写 db（db 层仍是原始旧形状）
        async with async_pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT open_tools FROM mcp_keys WHERE id = %s",
                (created["id"],),
            )).fetchone()
        assert list(row["open_tools"]) == ["search_knowledge", "get_evidence"]


@pytest.mark.asyncio
async def test_verify_admin_without_bindings_200(async_pool, kbdb):
    """admin 无绑定行（can_create_in_domain 全通）→ 验钥仍 200。"""
    async with await _client(async_pool) as c:
        admin = await _mk_user(async_pool, site_role="admin")
        created = await _create(c, f"adm-{_suffix()}", "odn",
                                kb_headers(admin["username"]))
        assert await kbdb.list_user_domains(user_id=admin["id"]) == []
        r = await c.post(VERIFY, json={"key": created["key"]}, headers=INTERNAL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["key_domain"] == "odn"
