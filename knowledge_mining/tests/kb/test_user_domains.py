"""51号批次1：user_domains 表结构冒烟（幂等插入）。"""
import pytest

from knowledge_mining.mining.kb.db import KbDB


@pytest.fixture
def kbdb(async_pool):
    return KbDB(async_pool)


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
    async with kbdb._pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO kb_users (id, username, site_role, created_at)
               VALUES (%s, %s, 'member', %s)
               ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username
               RETURNING id""",
            ("u_dom_1", "dom_user_1", "2026-09-17T00:00:00Z"),
        )
        uid = (await cur.fetchone())["id"]
    await kbdb.set_user_domains(user_id=uid, domains=["generic", "odn"])
    assert await kbdb.list_user_domains(user_id=uid) == ["generic", "odn"]
    # 覆盖式：重复与去重
    await kbdb.set_user_domains(user_id=uid, domains=["odn", "odn", "generic"])
    assert await kbdb.list_user_domains(user_id=uid) == ["generic", "odn"]


@pytest.mark.asyncio
async def test_can_create_in_domain(kbdb):
    async with kbdb._pool.connection() as conn:
        for uid, uname, role in (
            ("u_dom_2", "dom_user_2", "member"),
            ("u_dom_3", "dom_user_3", "admin"),
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
async def test_count_kbs_by_domain(kbdb):
    assert await kbdb.count_kbs_by_domain(domain="generic") >= 0  # 类型与无异常
