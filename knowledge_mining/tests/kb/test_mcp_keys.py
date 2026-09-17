"""51号批次2：mcp_keys 表结构冒烟 + 钥匙方法组。"""
import hashlib
import uuid

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


# ------------------------------------------------------------ 钥匙方法组（T2）
def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _mk_user(kbdb, uid: str, username: str) -> None:
    async with kbdb._pool.connection() as conn:
        await conn.execute(
            """INSERT INTO kb_users (id, username, site_role, created_at)
               VALUES (%s, %s, 'member', '2026-09-17T00:00:00Z')
               ON CONFLICT (id) DO NOTHING""",
            (uid, username),
        )


@pytest.mark.asyncio
async def test_mcp_key_lifecycle(kbdb):
    """主链路：create/get → 开放库域防线 → 验钥节流 → list 聚合 → rotate → revoke。"""
    s = _suffix()
    uid = f"u_mk_1_{s}"
    await _mk_user(kbdb, uid, f"mk_user_1_{s}")
    # 两个域各一个 active 库（generic 钥匙域）
    kb_a = await kbdb.create_kb(domain="generic", name=f"mk-a-{s}", owner_id=uid)
    kb_b = await kbdb.create_kb(domain="generic", name=f"mk-b-{s}", owner_id=uid)
    kb_c = await kbdb.create_kb(domain="odn", name=f"mk-c-{s}", owner_id=uid)
    key_id = uuid.uuid4().hex
    h1 = _hash(f"token1-{s}")
    created = await kbdb.create_mcp_key(
        user_id=uid, name=f"key-{s}", domain="generic",
        key_hash=h1, key_prefix=h1[:8], key_id=key_id,
    )
    assert created["id"] == key_id
    assert created["domain"] == "generic"
    assert created["status"] == "active"

    got = await kbdb.get_mcp_key(key_id=key_id)
    assert got is not None and got["id"] == key_id
    assert got["open_tools"] is None and got["instructions"] is None
    assert got["tool_descriptions"] is None
    assert await kbdb.get_mcp_key(key_id="no_such_key") is None
    assert await kbdb.count_active_mcp_keys(user_id=uid) == 1

    # 开放库域防线：越域 kb_c 被拒（SQL 层）
    effective = await kbdb.replace_mcp_key_open_kbs(
        key_id=key_id, kb_ids=[kb_a["id"], kb_b["id"], kb_c["id"]],
    )
    assert set(effective) == {kb_a["id"], kb_b["id"]}
    assert set(await kbdb.key_open_kb_ids(key_id=key_id)) == set(effective)

    # 读侧域过滤：直插越域开放行（绕过 replace 防线）也不出现在 key_open_kb_ids
    async with kbdb._pool.connection() as conn:
        await conn.execute(
            "INSERT INTO mcp_key_open_kbs (key_id, kb_id) VALUES (%s, %s)",
            (key_id, kb_c["id"]),
        )
    assert set(await kbdb.key_open_kb_ids(key_id=key_id)) == set(effective)
    async with kbdb._pool.connection() as conn:
        await conn.execute(
            "DELETE FROM mcp_key_open_kbs WHERE key_id = %s AND kb_id = %s",
            (key_id, kb_c["id"]),
        )

    # 验钥：节流窗口内二次调用仍命中（只读回退分支）
    v1 = await kbdb.find_mcp_key_by_hash(h1)
    assert v1 is not None and v1["key_id"] == key_id
    assert v1["username"] == f"mk_user_1_{s}"
    assert v1["domain"] == "generic"
    assert v1["open_tools"] is None
    assert {k["id"] for k in v1["open_kbs"]} == {kb_a["id"], kb_b["id"]}
    v2 = await kbdb.find_mcp_key_by_hash(h1)
    assert v2 is not None and v2["key_id"] == key_id
    assert await kbdb.find_mcp_key_by_hash(_hash(f"unknown-{s}")) is None

    # list 聚合；另一把不同域钥匙互不串
    key2 = uuid.uuid4().hex
    h2 = _hash(f"token2-{s}")
    await kbdb.create_mcp_key(
        user_id=uid, name=f"key2-{s}", domain="odn",
        key_hash=h2, key_prefix=h2[:8], key_id=key2,
    )
    await kbdb.replace_mcp_key_open_kbs(key_id=key2, kb_ids=[kb_a["id"], kb_c["id"]])
    keys = await kbdb.list_mcp_keys(user_id=uid)
    assert len(keys) == 2
    by_dom = {k["domain"]: k for k in keys}
    assert set(by_dom["generic"]["open_kb_ids"]) == {kb_a["id"], kb_b["id"]}
    assert set(by_dom["odn"]["open_kb_ids"]) == {kb_c["id"]}
    assert await kbdb.count_active_mcp_keys(user_id=uid) == 2

    # rotate：旧 hash 失效、新 hash 命中
    h3 = _hash(f"token3-{s}")
    rotated = await kbdb.rotate_mcp_key(key_id=key_id, key_hash=h3, key_prefix=h3[:8])
    assert rotated is not None and rotated["rotated_at"] is not None
    assert await kbdb.find_mcp_key_by_hash(h1) is None
    v3 = await kbdb.find_mcp_key_by_hash(h3)
    assert v3 is not None and v3["key_id"] == key_id

    # revoke：一次性；active-only 验钥
    assert await kbdb.revoke_mcp_key(key_id=key_id) is True
    assert await kbdb.revoke_mcp_key(key_id=key_id) is False
    assert await kbdb.find_mcp_key_by_hash(h3) is None
    assert await kbdb.rotate_mcp_key(
        key_id=key_id, key_hash=_hash(f"t4-{s}"), key_prefix="x"
    ) is None
    assert await kbdb.count_active_mcp_keys(user_id=uid) == 1


@pytest.mark.asyncio
async def test_update_mcp_key_config_partial(kbdb):
    """COALESCE 语义：None 不动该字段；三字段独立更新。"""
    s = _suffix()
    uid = f"u_mk_2_{s}"
    await _mk_user(kbdb, uid, f"mk_user_2_{s}")
    key_id = uuid.uuid4().hex
    h = _hash(f"cfg-{s}")
    await kbdb.create_mcp_key(
        user_id=uid, name=f"cfg-key-{s}", domain="generic",
        key_hash=h, key_prefix=h[:8], key_id=key_id,
    )
    await kbdb.update_mcp_key_config(
        key_id=key_id,
        open_tools=["get_knowledge"], instructions="你好", tool_descriptions=None,
    )
    got = await kbdb.get_mcp_key(key_id=key_id)
    assert got["open_tools"] == ["get_knowledge"]
    assert got["instructions"] == "你好"
    assert got["tool_descriptions"] is None
    # 只改 descriptions，其余不动
    await kbdb.update_mcp_key_config(
        key_id=key_id, open_tools=None, instructions=None,
        tool_descriptions={"get_knowledge": "查询知识"},
    )
    got = await kbdb.get_mcp_key(key_id=key_id)
    assert got["open_tools"] == ["get_knowledge"]
    assert got["instructions"] == "你好"
    assert got["tool_descriptions"] == {"get_knowledge": "查询知识"}
    # 验钥路径带出配置
    v = await kbdb.find_mcp_key_by_hash(h)
    assert v["open_tools"] == ["get_knowledge"]
    assert v["instructions"] == "你好"
