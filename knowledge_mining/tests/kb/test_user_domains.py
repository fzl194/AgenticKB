"""51号批次1：user_domains 表结构冒烟（幂等插入）+ DbDB 绑定方法五件套。"""
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.routes.auth import router as auth_router
from knowledge_mining.tests.conftest import kb_headers


@pytest.fixture
def kbdb(async_pool):
    return KbDB(async_pool)


def _suffix() -> str:
    """随机后缀——username 唯一约束防撞共享测试库的历史数据。"""
    return uuid.uuid4().hex[:8]


@pytest.mark.asyncio
async def test_user_domains_table_exists_and_upsert(kbdb):
    async with kbdb._pool.connection() as conn:
        for _ in range(2):
            await conn.execute(
                "INSERT INTO user_domains (user_id, domain) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                ("u_test_1", "generic"),
            )
        cur = await conn.execute(
            "SELECT count(*) AS n FROM user_domains WHERE user_id = %s", ("u_test_1",)
        )
        row = await cur.fetchone()
    assert row["n"] == 1


@pytest.mark.asyncio
async def test_list_and_set_user_domains(kbdb):
    s = _suffix()
    async with kbdb._pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO kb_users (id, username, site_role, created_at)
               VALUES (%s, %s, 'member', %s)
               ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username
               RETURNING id""",
            ("u_dom_1", f"dom_user_1_{s}", "2026-09-17T00:00:00Z"),
        )
        uid = (await cur.fetchone())["id"]
    await kbdb.set_user_domains(user_id=uid, domains=["generic", "odn"])
    assert await kbdb.list_user_domains(user_id=uid) == ["generic", "odn"]
    # 覆盖式：重复与去重
    await kbdb.set_user_domains(user_id=uid, domains=["odn", "odn", "generic"])
    assert await kbdb.list_user_domains(user_id=uid) == ["generic", "odn"]


@pytest.mark.asyncio
async def test_list_users_includes_bound_domains(kbdb):
    """管理列表一次返回绑定域，页面首次加载即可稳定回显。"""
    s = _suffix()
    user = await kbdb.create_user(username=f"list_domains_{s}", site_role="member")
    unbound = await kbdb.create_user(username=f"list_unbound_{s}", site_role="member")
    await kbdb.set_user_domains(user_id=user["id"], domains=["generic", "odn"])

    listed = await kbdb.list_users()
    row = next(item for item in listed if item["id"] == user["id"])
    unbound_row = next(item for item in listed if item["id"] == unbound["id"])

    assert row["domains"] == ["generic", "odn"]
    assert unbound_row["domains"] == []


@pytest.mark.asyncio
async def test_bind_domain_idempotent(kbdb):
    s = _suffix()
    async with kbdb._pool.connection() as conn:
        await conn.execute(
            """INSERT INTO kb_users (id, username, site_role, created_at)
               VALUES (%s, %s, 'member', %s) ON CONFLICT (id) DO NOTHING""",
            ("u_dom_bind", f"dom_user_bind_{s}", "2026-09-17T00:00:00Z"),
        )
    for _ in range(2):
        await kbdb.bind_domain(user_id="u_dom_bind", domain="generic")
    assert await kbdb.list_user_domains(user_id="u_dom_bind") == ["generic"]


@pytest.mark.asyncio
async def test_can_create_in_domain(kbdb):
    s = _suffix()
    async with kbdb._pool.connection() as conn:
        for uid, uname, role in (
            ("u_dom_2", f"dom_user_2_{s}", "member"),
            ("u_dom_3", f"dom_user_3_{s}", "admin"),
        ):
            await conn.execute(
                """INSERT INTO kb_users (id, username, site_role, created_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET site_role = EXCLUDED.site_role""",
                (uid, uname, role, "2026-09-17T00:00:00Z"),
            )
        await conn.execute(
            "INSERT INTO user_domains (user_id, domain) VALUES (%s, 'generic') ON CONFLICT DO NOTHING",
            ("u_dom_2",),
        )
    assert await kbdb.can_create_in_domain(user_id="u_dom_2", domain="generic") is True
    assert await kbdb.can_create_in_domain(user_id="u_dom_2", domain="odn") is False
    # admin 免绑定全通
    assert await kbdb.can_create_in_domain(user_id="u_dom_3", domain="odn") is True


@pytest.mark.asyncio
async def test_count_kbs_by_domain_active_only(kbdb):
    """count 只计 active；软删（status='deleted'）不计。随机域名防共享库撞数。"""
    s = _suffix()
    domain = f"dom_count_{s}"
    async with kbdb._pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO kb_users (id, username, site_role, created_at)
               VALUES (%s, %s, 'member', %s) ON CONFLICT (id) DO NOTHING
               RETURNING id""",
            ("u_dom_count", f"dom_user_count_{s}", "2026-09-17T00:00:00Z"),
        )
        owner = (await cur.fetchone())["id"]
        base = (
            "INSERT INTO knowledge_bases "
            "(id, domain, name, owner_id, status, created_at, updated_at, deleted_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        )
        await conn.execute(
            base, (f"kb_dom_a_{s}", domain, f"kb-a-{s}", owner, "active",
                   "2026-09-17T00:00:00Z", "2026-09-17T00:00:00Z", None),
        )
        await conn.execute(
            base, (f"kb_dom_d_{s}", domain, f"kb-d-{s}", owner, "deleted",
                   "2026-09-17T00:00:00Z", "2026-09-17T00:00:00Z", "2026-09-17T00:00:00Z"),
        )
    assert await kbdb.count_kbs_by_domain(domain=domain) == 1


async def _mk_user(kbdb, uid: str, username: str, *, site_role: str = "member") -> None:
    async with kbdb._pool.connection() as conn:
        await conn.execute(
            """INSERT INTO kb_users (id, username, site_role, created_at)
               VALUES (%s, %s, %s, '2026-09-17T00:00:00Z')
               ON CONFLICT (id) DO NOTHING""",
            (uid, username, site_role),
        )


@pytest.mark.asyncio
async def test_public_kb_visibility_requires_binding(kbdb):
    """public 库域内化：未绑定域的用户不可见；owner 不受影响（安全网）；绑定后可见。"""
    s = _suffix()
    uid, uid2 = f"u_vis_1_{s}", f"u_vis_2_{s}"
    await _mk_user(kbdb, uid, f"vis_user_1_{s}")
    await _mk_user(kbdb, uid2, f"vis_user_2_{s}")
    kb = await kbdb.create_kb(
        domain="generic", name=f"vis-public-{s}", owner_id=uid2, visibility="public",
    )
    kb_id = kb["id"]
    try:
        # 未绑定 → 不可见（list_visible_kb_ids / is_visible）
        assert kb_id not in await kbdb.list_visible_kb_ids(
            user_id=uid, domain="generic"
        )
        assert await kbdb.is_visible(kb_id=kb_id, user_id=uid) is False
        # owner 安全网：owner 未绑定该域仍可见（owner 分支不分域）
        assert await kbdb.is_visible(kb_id=kb_id, user_id=uid2) is True
        # 绑定后 → 可见
        await kbdb.bind_domain(user_id=uid, domain="generic")
        assert kb_id in await kbdb.list_visible_kb_ids(
            user_id=uid, domain="generic"
        )
        assert await kbdb.is_visible(kb_id=kb_id, user_id=uid) is True
    finally:
        async with kbdb._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM knowledge_bases WHERE id = %s", (kb_id,)
            )


@pytest.mark.asyncio
async def test_create_kb_requires_binding(kbdb):
    """service 层建库收敛：非 admin 未绑定域 → DomainNotBound；绑定后成功；admin 免绑定。"""
    from knowledge_mining.mining.kb.services.kb_service import KbService, DomainNotBound

    svc = KbService(kbdb)
    s = _suffix()
    uid, admin_uid = f"u_cb_1_{s}", f"u_cb_2_{s}"
    await _mk_user(kbdb, uid, f"cb_user_1_{s}")
    await _mk_user(kbdb, admin_uid, f"cb_user_2_{s}", site_role="admin")
    # 未绑定 → DomainNotBound
    with pytest.raises(DomainNotBound):
        await svc.create_kb(domain="generic", name=f"绑定测试库{s}", owner_id=uid)
    # 绑定后 → 成功
    await kbdb.bind_domain(user_id=uid, domain="generic")
    kb = await svc.create_kb(domain="generic", name=f"绑定测试库{s}", owner_id=uid)
    assert kb["domain"] == "generic"
    # admin 不绑域，create_kb 不被拦
    kb_admin = await svc.create_kb(domain="odn", name=f"绑定测试库{s}", owner_id=admin_uid)
    assert kb_admin["domain"] == "odn"
    # 清理：避免共享测试库残留
    async with kbdb._pool.connection() as conn:
        await conn.execute(
            "DELETE FROM knowledge_bases WHERE id IN (%s, %s)", (kb["id"], kb_admin["id"])
        )


@pytest.mark.asyncio
async def test_create_member_auto_binds_default_domain(async_pool):
    """51号批次1：新用户（member）创建即自动绑默认域——零绑定视为缺陷。"""
    from knowledge_mining.mining.kb.services.user_service import UserService
    from knowledge_mining.mining.infra.domain_pack import get_default_domain

    svc = UserService(KbDB(async_pool))
    u = await svc.create_user(username=f"auto_bind_{_suffix()}", site_role="member")
    kbdb = KbDB(async_pool)
    bound = await kbdb.list_user_domains(user_id=u["id"])
    assert get_default_domain() in bound


# ---------------------------------------------------------------- admin 域分配端点

async def _client(async_pool):
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    app = FastAPI()
    app.state.pg_pool = async_pool
    app.state.db_config = MiningDbConfig()
    app.include_router(auth_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _mk_admin(async_pool):
    db = KbDB(async_pool)
    return await db.create_user(username=f"root_{_suffix()}", password_hash="x", site_role="admin")


@pytest.mark.asyncio
async def test_admin_get_and_assign_user_domains(async_pool):
    """GET 返回 list；POST 覆盖式分配后 sorted == 传入（替换自动绑定的默认域）。"""
    db = KbDB(async_pool)
    admin = await _mk_admin(async_pool)
    target = await db.create_user(username=f"dom_target_{_suffix()}", site_role="member")
    async with await _client(async_pool) as c:
        r = await c.get(f"/api/kb/admin/users/{target['id']}/domains",
                        headers=kb_headers(admin["username"]))
        assert r.status_code == 200, r.text
        assert isinstance(r.json()["domains"], list)

        r = await c.post(f"/api/kb/admin/users/{target['id']}/domains",
                         json={"domains": ["generic", "odn"]},
                         headers=kb_headers(admin["username"]))
        assert r.status_code == 200, r.text
        assert sorted(r.json()["domains"]) == ["generic", "odn"]
        # 覆盖式：再分配单域替换
        r = await c.post(f"/api/kb/admin/users/{target['id']}/domains",
                         json={"domains": ["odn"]},
                         headers=kb_headers(admin["username"]))
        assert r.status_code == 200, r.text
        assert r.json()["domains"] == ["odn"]
    assert await db.list_user_domains(user_id=target["id"]) == ["odn"]


@pytest.mark.asyncio
async def test_admin_assign_empty_domains_422(async_pool):
    """51号：不允许零绑定——空集（显式空或全空白）→ 422。"""
    db = KbDB(async_pool)
    admin = await _mk_admin(async_pool)
    target = await db.create_user(username=f"dom_empty_{_suffix()}", site_role="member")
    async with await _client(async_pool) as c:
        r = await c.post(f"/api/kb/admin/users/{target['id']}/domains",
                         json={"domains": []},
                         headers=kb_headers(admin["username"]))
        assert r.status_code == 422, r.text
        r = await c.post(f"/api/kb/admin/users/{target['id']}/domains",
                         json={"domains": ["  "]},
                         headers=kb_headers(admin["username"]))
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_admin_assign_invalid_domain_400(async_pool):
    db = KbDB(async_pool)
    admin = await _mk_admin(async_pool)
    target = await db.create_user(username=f"dom_bad_{_suffix()}", site_role="member")
    async with await _client(async_pool) as c:
        r = await c.post(f"/api/kb/admin/users/{target['id']}/domains",
                         json={"domains": ["no_such_domain"]},
                         headers=kb_headers(admin["username"]))
        assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_user_domains_admin_only_403(async_pool):
    db = KbDB(async_pool)
    await _mk_admin(async_pool)
    member = await db.create_user(username=f"dom_member_{_suffix()}", site_role="member")
    async with await _client(async_pool) as c:
        r = await c.get("/api/kb/admin/users/someone/domains", headers=kb_headers(member["username"]))
        assert r.status_code == 403
        r = await c.post("/api/kb/admin/users/someone/domains", json={"domains": ["generic"]},
                         headers=kb_headers(member["username"]))
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_user_domains_user_not_found_404(async_pool):
    admin = await _mk_admin(async_pool)
    async with await _client(async_pool) as c:
        r = await c.get("/api/kb/admin/users/u_no_such_user/domains",
                        headers=kb_headers(admin["username"]))
        assert r.status_code == 404
        r = await c.post("/api/kb/admin/users/u_no_such_user/domains",
                         json={"domains": ["generic"]},
                         headers=kb_headers(admin["username"]))
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_assign_domains_to_admin_is_noop(async_pool):
    """admin 目标免绑定：幂等空操作，返回空集且不落 user_domains。"""
    db = KbDB(async_pool)
    admin = await _mk_admin(async_pool)
    async with await _client(async_pool) as c:
        r = await c.post(f"/api/kb/admin/users/{admin['id']}/domains",
                         json={"domains": ["generic"]},
                         headers=kb_headers(admin["username"]))
        assert r.status_code == 200, r.text
        assert r.json()["domains"] == []
    assert await db.list_user_domains(user_id=admin["id"]) == []


@pytest.mark.asyncio
async def test_document_readable_requires_binding(kbdb):
    """document_readable_by_user（属主库/引用库两张查询）同语义——public 亦须域绑定。"""
    s = _suffix()
    uid, uid2 = f"u_dr_1_{s}", f"u_dr_2_{s}"
    await _mk_user(kbdb, uid, f"dr_user_1_{s}")
    await _mk_user(kbdb, uid2, f"dr_user_2_{s}")
    kb = await kbdb.create_kb(
        domain="generic", name=f"dr-public-{s}", owner_id=uid2, visibility="public",
    )
    kb_id = kb["id"]
    doc = await kbdb.insert_document_identity(
        domain="generic", kb_id=kb_id,
        document_key=f"dr_doc_{s}.txt", document_name=f"dr_doc_{s}.txt",
        storage_path=f"/tmp/dr_{s}.txt",
    )
    doc_id = doc["id"]
    try:
        assert await kbdb.document_readable_by_user(doc_id, uid) is False
        await kbdb.bind_domain(user_id=uid, domain="generic")
        assert await kbdb.document_readable_by_user(doc_id, uid) is True
    finally:
        async with kbdb._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM asset_documents WHERE id = %s", (doc_id,)
            )
            await conn.execute(
                "DELETE FROM knowledge_bases WHERE id = %s", (kb_id,)
            )


# ---------------------------------------------------------------- internal endpoints (Task 8)

async def _internal_client(async_pool):
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig

    app = FastAPI()
    app.state.pg_pool = async_pool
    app.state.db_config = MiningDbConfig()
    app.include_router(auth_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_internal_user_domains_roundtrip(async_pool):
    """51号批次1：内部端点用户名→绑定域。member/admin 都返回绑定行；不存在→[]。"""
    db = KbDB(async_pool)
    s = _suffix()
    m = await db.create_user(username=f"iu_member_{s}", site_role="member")
    a = await db.create_user(username=f"iu_admin_{s}", site_role="admin")
    await db.set_user_domains(user_id=m["id"], domains=["generic", "odn"])
    await db.set_user_domains(user_id=a["id"], domains=["odn"])
    async with await _internal_client(async_pool) as c:
        r = await c.get(f"/api/kb/internal/users/iu_member_{s}/domains",
                        headers=kb_headers("i"))
        assert r.status_code == 200, r.text
        assert sorted(r.json()["domains"]) == ["generic", "odn"]
        # admin 不按角色短路——main_control 自己按 JWT role 处理
        r = await c.get(f"/api/kb/internal/users/iu_admin_{s}/domains",
                        headers=kb_headers("i"))
        assert r.status_code == 200, r.text
        assert r.json()["domains"] == ["odn"]
        # 不存在的用户名 → 空列表不报错
        r = await c.get("/api/kb/internal/users/nobody_here_xxx/domains",
                        headers=kb_headers("i"))
        assert r.status_code == 200, r.text
        assert r.json()["domains"] == []
        # 无内部头 → 401
        r = await c.get(f"/api/kb/internal/users/iu_member_{s}/domains")
        assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_internal_kb_count(async_pool):
    """51号批次1：内部端点域名→active KB 数。"""
    db = KbDB(async_pool)
    s = _suffix()
    domain = f"dom_cnt_{s}"
    u = await db.create_user(username=f"iu_cnt_{s}", site_role="member")
    kb = await db.create_kb(domain=domain, name=f"iu-cnt-{s}", owner_id=u["id"])
    try:
        async with await _internal_client(async_pool) as c:
            r = await c.get(f"/api/kb/internal/domains/{domain}/kb-count",
                            headers=kb_headers("i"))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["domain"] == domain
            assert isinstance(body["kb_count"], int) and body["kb_count"] >= 1
            # 无头 → 401
            r = await c.get(f"/api/kb/internal/domains/{domain}/kb-count")
            assert r.status_code in (401, 403)
    finally:
        async with db._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM knowledge_bases WHERE id = %s", (kb["id"],)
            )
