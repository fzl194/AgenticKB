"""51号批次1：user_domains 表与 DbDB 绑定方法测试。"""
import pytest

from knowledge_mining.mining.kb.db import KbDB


@pytest.fixture
def kbdb(async_pool):
    return KbDB(async_pool)


@pytest.mark.asyncio
async def test_user_domains_table_exists_and_upsert(kbdb):
    async with kbdb._pool.connection() as conn:
        await conn.execute(
            "INSERT INTO user_domains (user_id, domain) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            ("u_test_1", "generic"),
        )
        cur = await conn.execute(
            "SELECT count(*) AS n FROM user_domains WHERE user_id = %s", ("u_test_1",)
        )
        row = await cur.fetchone()
    assert row["n"] == 1
