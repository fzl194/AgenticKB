"""51号批次2：mcp_keys 表结构冒烟 + 钥匙方法组。

（后续任务将在本文件追加钥匙 CRUD/轮换/验钥等方法组测试。）
"""
import pytest

from knowledge_mining.mining.kb.db import KbDB


@pytest.fixture
def kbdb(async_pool):
    return KbDB(async_pool)


@pytest.mark.asyncio
async def test_mcp_keys_tables_exist(kbdb):
    async with kbdb._pool.connection() as conn:
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'mcp_keys'"
        )
        cols = {r["column_name"] for r in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'mcp_key_open_kbs'"
        )
        cols2 = {r["column_name"] for r in await cur.fetchall()}
    assert {"id", "user_id", "name", "domain", "key_hash", "key_prefix", "status",
            "open_tools", "instructions", "tool_descriptions",
            "created_at", "rotated_at", "last_used_at"} <= cols
    assert {"key_id", "kb_id", "granted_at"} == cols2
