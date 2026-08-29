"""阶段 A（批次5）：MCP 用户接入——密钥/轮换/开放库。

纯逻辑部分本地可跑；PG 集成部分走 async_pool（无本地测试库时 env-error，
SQL 语义由部署后 E2E 兜底——与 kb 侧既有测试同模式）。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.mcp_access_service import (
    KEY_PREFIX_TAG,
    McpAccessError,
    McpAccessService,
    generate_mcp_key,
    hash_mcp_key,
)

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------- 纯逻辑（本地）

def test_generate_mcp_key_shape() -> None:
    plaintext, key_hash, prefix = generate_mcp_key()
    assert plaintext.startswith(KEY_PREFIX_TAG)
    assert len(plaintext) == len(KEY_PREFIX_TAG) + 64  # 32 bytes hex
    assert prefix == plaintext[:8]
    assert key_hash == hash_mcp_key(plaintext)
    assert len(key_hash) == 64  # sha256 hex
    # 两次生成必不同（随机）
    other, _, _ = generate_mcp_key()
    assert other != plaintext


async def test_verify_rejects_non_prefixed_key() -> None:
    class _Db(KbDB):
        def __init__(self) -> None:  # 不走父类构造（无 pool）
            pass

        async def verify_mcp_key(self, key_hash: str, **_):
            raise AssertionError("must not hit db for non-prefixed key")

    svc = McpAccessService(_Db())  # type: ignore[arg-type]
    assert await svc.verify_key("not-a-kbm-key") is None


class _FakeDb:
    """service 层所需最小面；records 调用轨迹供断言。"""

    def __init__(self, visible: set[str] | None = None) -> None:
        self.visible = visible or set()
        self.rotated: list[tuple[str, str, str]] = []
        self.replaced: list[tuple[str, list[str]]] = []

    async def is_visible(self, *, kb_id: str, user_id: str) -> bool:
        return kb_id in self.visible

    async def upsert_mcp_key(self, user_id, *, key_hash, key_prefix):
        self.rotated.append((user_id, key_hash, key_prefix))
        return {"user_id": user_id, "key_prefix": key_prefix,
                "status": "active", "rotated_at": "2026-08-28T00:00:00+00:00"}

    async def replace_open_kbs(self, user_id, kb_ids):
        self.replaced.append((user_id, kb_ids))
        return kb_ids


async def test_rotate_returns_plaintext_once_and_overwrites() -> None:
    db = _FakeDb()
    svc = McpAccessService(db)  # type: ignore[arg-type]
    r1 = await svc.rotate_key(user_id="u1")
    r2 = await svc.rotate_key(user_id="u1")
    assert r1["key"] != r2["key"]
    assert [call[0] for call in db.rotated] == ["u1", "u1"]
    assert db.rotated[0][1] != db.rotated[1][1]  # hash 被覆盖=旧钥立即失效


async def test_replace_open_kbs_rejects_invisible_kb_entirely() -> None:
    db = _FakeDb(visible={"kb-ok"})
    svc = McpAccessService(db)  # type: ignore[arg-type]
    with pytest.raises(McpAccessError, match="not visible"):
        await svc.replace_open_kbs(user_id="u1", kb_ids=["kb-ok", "kb-hidden"])
    assert db.replaced == []  # 整单拒绝，不接受部分生效


async def test_replace_open_kbs_dedupes() -> None:
    db = _FakeDb(visible={"kb-a", "kb-b"})
    svc = McpAccessService(db)  # type: ignore[arg-type]
    final = await svc.replace_open_kbs(
        user_id="u1", kb_ids=["kb-a", "kb-b", "kb-a"],
    )
    assert final == ["kb-a", "kb-b"]
    assert db.replaced == [("u1", ["kb-a", "kb-b"])]


# ------------------------------------------------ 批次7：config 校验（纯逻辑）

class _ConfigDb(_FakeDb):
    def __init__(self) -> None:
        super().__init__()
        self.config_updates: list[tuple] = []

    async def update_mcp_config(self, user_id, *, open_tools, instructions, tool_descriptions):
        self.config_updates.append((user_id, open_tools, instructions, tool_descriptions))

    async def get_mcp_access(self, user_id):
        return None  # update_config 成功后回读状态；测试只关心写参数


async def test_config_rejects_unknown_tool_names() -> None:
    svc = McpAccessService(_ConfigDb())  # type: ignore[arg-type]
    with pytest.raises(McpAccessError, match="unknown tool names"):
        await svc.update_config(user_id="u1", open_tools=["search_knowledge", "hack_tool"])


async def test_config_requires_at_least_one_tool() -> None:
    svc = McpAccessService(_ConfigDb())  # type: ignore[arg-type]
    with pytest.raises(McpAccessError, match="至少保留一个"):
        await svc.update_config(user_id="u1", open_tools=[])


async def test_config_normalizes_blank_instructions_to_default() -> None:
    db = _ConfigDb()
    svc = McpAccessService(db)  # type: ignore[arg-type]
    await svc.update_config(user_id="u1", instructions="   ")
    assert db.config_updates[0][2] is None  # 空白=恢复默认文案（NULL）


async def test_config_rejects_oversized_descriptions() -> None:
    db = _ConfigDb()
    svc = McpAccessService(db)  # type: ignore[arg-type]
    with pytest.raises(McpAccessError, match="描述过长"):
        await svc.update_config(
            user_id="u1", tool_descriptions={"search_knowledge": "x" * 5000})


# ---------------------------------------------------------- PG 集成（async_pool）

async def test_mcp_key_lifecycle(async_pool):
    """建钥→验钥命中→轮换→旧 hash 立即 miss→开放库覆盖生效。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("mcp-owner")
    kb = await db.create_kb(
        domain="cloud_core_network", name="mcp-access-lifecycle",
        owner_id=owner["id"],
    )

    # 未配置 → None
    assert await db.get_mcp_access(owner["id"]) is None

    # 建钥
    plaintext, key_hash, key_prefix = generate_mcp_key()
    row = await db.upsert_mcp_key(
        owner["id"], key_hash=key_hash, key_prefix=key_prefix,
    )
    assert row["status"] == "active"

    # 开放库
    await db.replace_open_kbs(owner["id"], [kb["id"]])

    # 验钥命中 + 开放库带出
    verified = await db.verify_mcp_key(key_hash)
    assert verified is not None
    assert verified["username"] == "mcp-owner"
    assert verified["open_kb_ids"] == [kb["id"]]

    # 轮换：旧 hash 立即失效
    plaintext2, key_hash2, key_prefix2 = generate_mcp_key()
    await db.upsert_mcp_key(
        owner["id"], key_hash=key_hash2, key_prefix=key_prefix2,
    )
    assert await db.verify_mcp_key(key_hash) is None
    verified2 = await db.verify_mcp_key(key_hash2)
    assert verified2 is not None and verified2["user_id"] == owner["id"]

    # 覆盖开放库（清空）
    final = await db.replace_open_kbs(owner["id"], [])
    assert final == []
