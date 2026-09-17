"""51号批次1：user_domains 表结构冒烟（幂等插入）+ DbDB 绑定方法五件套。"""
import uuid

import pytest

from knowledge_mining.mining.kb.db import KbDB


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
