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
    # 列表视图要显示最近使用——SELECT 必须带出 last_used_at（前面验钥已触写过）
    assert by_dom["generic"]["last_used_at"] is not None
    assert "last_used_at" in await kbdb.get_mcp_key(key_id=key_id)
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


# ------------------------------------------------- T3：McpKeyService（FakeDb）
from knowledge_mining.mining.kb.services.mcp_key_service import (  # noqa: E402
    KEY_PREFIX_TAG,
    KeyDomainNotBound,
    KeyLimitExceeded,
    KeyNameConflict,
    KeyNotFound,
    KeyRevoked,
    McpKeyError,
    McpKeyService,
    normalize_legacy_open_tools,
)
from psycopg.errors import UniqueViolation  # noqa: E402


class _KeyFakeDb:
    """McpKeyService 所需最小面；记录调用轨迹供断言。"""

    def __init__(
        self,
        *,
        can_domain: bool = True,
        active_count: int = 0,
        visible: set[str] | None = None,
        keys: dict[str, dict] | None = None,
    ) -> None:
        self.can_domain = can_domain
        self.active_count = active_count
        self.visible = visible if visible is not None else set()
        self.keys = keys if keys is not None else {}
        self.created: list[dict] = []
        self.rotated: list[tuple] = []
        self.revoked: list[str] = []
        self.replaced: list[tuple] = []
        self.config_updates: list[tuple] = []

    async def can_create_in_domain(self, *, user_id: str, domain: str) -> bool:
        return self.can_domain

    async def count_active_mcp_keys(self, *, user_id: str) -> int:
        return self.active_count

    async def create_mcp_key(self, **kwargs):
        if any(k["name"] == kwargs["name"] for k in self.created):
            raise UniqueViolation("dup")
        self.created.append(kwargs)
        return {"id": kwargs["key_id"], "user_id": kwargs["user_id"],
                "name": kwargs["name"], "domain": kwargs["domain"],
                "key_prefix": kwargs["key_prefix"], "status": "active",
                "created_at": "2026-09-17T00:00:00+00:00"}

    async def get_mcp_key(self, *, key_id: str):
        return self.keys.get(key_id)

    async def rotate_mcp_key(self, *, key_id: str, key_hash: str, key_prefix: str):
        self.rotated.append((key_id, key_hash, key_prefix))
        return {"id": key_id, "key_prefix": key_prefix,
                "rotated_at": "2026-09-17T01:00:00+00:00"}

    async def revoke_mcp_key(self, *, key_id: str) -> bool:
        self.revoked.append(key_id)
        return True

    async def is_visible(self, *, kb_id: str, user_id: str) -> bool:
        return kb_id in self.visible

    async def replace_mcp_key_open_kbs(self, *, key_id: str, kb_ids):
        self.replaced.append((key_id, kb_ids))
        return kb_ids

    async def update_mcp_key_config(self, *, key_id, open_tools, instructions,
                                    tool_descriptions):
        self.config_updates.append((key_id, open_tools, instructions,
                                    tool_descriptions))

    async def key_open_kb_ids(self, *, key_id: str):
        return []


def _key_row(**overrides) -> dict:
    row = {"id": "k1", "user_id": "u1", "name": "n", "domain": "generic",
           "key_prefix": "kbm_1234", "status": "active", "open_tools": None,
           "instructions": None, "tool_descriptions": None}
    row.update(overrides)
    return row


def _svc(db) -> McpKeyService:
    return McpKeyService(db)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_returns_plaintext_once_and_only_hash_persisted() -> None:
    db = _KeyFakeDb()
    r = await _svc(db).create_key(
        user_id="u1", name="  我的钥匙  ", domain="generic", is_admin=False,
    )
    assert r["key"].startswith(KEY_PREFIX_TAG)
    assert db.created[0]["name"] == "我的钥匙"  # strip 后落库
    assert "key" not in db.created[0]           # 明文不落库，只有 hash/prefix
    assert db.created[0]["key_hash"] and db.created[0]["key_prefix"]


@pytest.mark.asyncio
async def test_create_rejects_bad_names() -> None:
    db = _KeyFakeDb()
    svc = _svc(db)
    with pytest.raises(McpKeyError, match="不能为空"):
        await svc.create_key(user_id="u1", name="   ", domain="generic",
                             is_admin=False)
    with pytest.raises(McpKeyError, match="过长"):
        await svc.create_key(user_id="u1", name="x" * 65, domain="generic",
                             is_admin=False)
    with pytest.raises(McpKeyError, match="控制字符"):
        await svc.create_key(user_id="u1", name="bad\x01name", domain="generic",
                             is_admin=False)
    # Cf 类：零宽空格（U+200B）与 RTL override（U+202E）——视觉欺骗载体
    with pytest.raises(McpKeyError, match="控制字符"):
        await svc.create_key(user_id="u1", name="bad​name", domain="generic",
                             is_admin=False)
    with pytest.raises(McpKeyError, match="控制字符"):
        await svc.create_key(user_id="u1", name="bad‮name", domain="generic",
                             is_admin=False)


@pytest.mark.asyncio
async def test_create_bad_domain_maps_to_mcp_key_error() -> None:
    """InvalidDomain 归族：坏 domain → McpKeyError（422），不裸 500。"""
    db = _KeyFakeDb()
    with pytest.raises(McpKeyError, match="未知的知识域"):
        await _svc(db).create_key(user_id="u1", name="k",
                                  domain="no_such_domain", is_admin=False)


@pytest.mark.asyncio
async def test_create_rejects_unbound_domain_and_admin_bypasses() -> None:
    db = _KeyFakeDb(can_domain=False)
    with pytest.raises(KeyDomainNotBound):
        await _svc(db).create_key(user_id="u1", name="k", domain="generic",
                                  is_admin=False)
    # admin 不触发域绑定检查（can_domain=False 仍可建）
    r = await _svc(db).create_key(user_id="u1", name="k", domain="generic",
                                  is_admin=True)
    assert r["key"].startswith(KEY_PREFIX_TAG)


@pytest.mark.asyncio
async def test_create_enforces_limit_ten() -> None:
    db = _KeyFakeDb(active_count=10)
    with pytest.raises(KeyLimitExceeded):
        await _svc(db).create_key(user_id="u1", name="k", domain="generic",
                                  is_admin=False)
    db.active_count = 9
    r = await _svc(db).create_key(user_id="u1", name="k", domain="generic",
                                  is_admin=False)
    assert r["status"] == "active"


@pytest.mark.asyncio
async def test_create_name_conflict_maps_unique_violation() -> None:
    db = _KeyFakeDb()
    db.created.append({"name": "k"})  # 已存在同名 → FakeDb 抛 UniqueViolation
    with pytest.raises(KeyNameConflict):
        await _svc(db).create_key(user_id="u1", name="k", domain="generic",
                                  is_admin=False)


@pytest.mark.asyncio
async def test_rotate_revoked_raises_and_others_key_not_found() -> None:
    db = _KeyFakeDb(keys={
        "k-revoked": _key_row(id="k-revoked", status="revoked"),
        "k-other": _key_row(id="k-other", user_id="someone-else"),
    })
    svc = _svc(db)
    with pytest.raises(KeyRevoked):
        await svc.rotate_key(user_id="u1", key_id="k-revoked")
    with pytest.raises(KeyNotFound):
        await svc.rotate_key(user_id="u1", key_id="k-other")
    with pytest.raises(KeyNotFound):
        await svc.revoke_key(user_id="u1", key_id="k-other")
    # 正常轮换返回明文
    db.keys["k-ok"] = _key_row(id="k-ok")
    r = await svc.rotate_key(user_id="u1", key_id="k-ok")
    assert r["key"].startswith(KEY_PREFIX_TAG)


@pytest.mark.asyncio
async def test_replace_open_kbs_drops_invisible_and_dedupes() -> None:
    db = _KeyFakeDb(visible={"kb-a", "kb-b"}, keys={"k1": _key_row()})
    final = await _svc(db).replace_open_kbs(
        user_id="u1", key_id="k1", kb_ids=["kb-a", "kb-gone", "kb-b", "kb-a"],
    )
    assert final == ["kb-a", "kb-b"]
    assert db.replaced == [("k1", ["kb-a", "kb-b"])]  # 不可见静默剔除


@pytest.mark.asyncio
async def test_update_config_validation_family() -> None:
    db = _KeyFakeDb(keys={"k1": _key_row()})
    svc = _svc(db)
    with pytest.raises(McpKeyError, match="unknown tool names"):
        await svc.update_config(user_id="u1", key_id="k1",
                                open_tools=["search_knowledge", "hack"],
                                instructions=None, tool_descriptions=None)
    with pytest.raises(McpKeyError, match="至少保留一个"):
        await svc.update_config(user_id="u1", key_id="k1", open_tools=[],
                                instructions=None, tool_descriptions=None)
    with pytest.raises(McpKeyError, match="提示词过长"):
        await svc.update_config(user_id="u1", key_id="k1", open_tools=None,
                                instructions="x" * 4001, tool_descriptions=None)
    with pytest.raises(McpKeyError, match="描述过长"):
        await svc.update_config(user_id="u1", key_id="k1", open_tools=None,
                                instructions=None,
                                tool_descriptions={"get_knowledge": "x" * 2001})
    # 空串 instructions 归 None（恢复默认）
    await svc.update_config(user_id="u1", key_id="k1", open_tools=None,
                            instructions="   ", tool_descriptions=None)
    assert db.config_updates[-1][2] is None
    # 归一化回读：get_key_status 把旧工具名归一成新名
    db.keys["k1"] = _key_row(open_tools=["get_content", "search_knowledge"])
    status = await svc.update_config(user_id="u1", key_id="k1",
                                     open_tools=None, instructions=None,
                                     tool_descriptions=None)
    assert status["open_tools"] == ["get_knowledge", "search_knowledge"]


@pytest.mark.asyncio
async def test_verify_key_rejects_non_prefixed() -> None:
    class _NoDb(_KeyFakeDb):
        async def find_mcp_key_by_hash(self, key_hash, **_):
            raise AssertionError("must not hit db for non-prefixed key")

    assert await _svc(_NoDb()).verify_key("not-a-kbm-key") is None


# --------------------------------------------- T3：McpKeyService（PG lifecycle）

@pytest.mark.asyncio
async def test_key_service_domain_scoped_lifecycle(kbdb):
    """验收门禁 5：双域用户两把钥匙互不串；解绑域 → domain_bound 翻 False。"""
    s = _suffix()
    uid = f"u_mks_{s}"
    await _mk_user(kbdb, uid, f"mks_user_{s}")
    await kbdb.set_user_domains(user_id=uid, domains=["generic", "odn"])
    kb_g = await kbdb.create_kb(domain="generic", name=f"mks-g-{s}", owner_id=uid)
    kb_o = await kbdb.create_kb(domain="odn", name=f"mks-o-{s}", owner_id=uid)

    svc = McpKeyService(kbdb)
    k1 = await svc.create_key(user_id=uid, name=f"g-{s}", domain="generic",
                              is_admin=False)
    k2 = await svc.create_key(user_id=uid, name=f"o-{s}", domain="odn",
                              is_admin=False)
    assert k1["key"].startswith(KEY_PREFIX_TAG)
    assert k2["key"].startswith(KEY_PREFIX_TAG)

    # 各域钥匙只收本域库（跨域勾选被 SQL 层防线剔除）
    eff1 = await svc.replace_open_kbs(
        user_id=uid, key_id=k1["id"], kb_ids=[kb_g["id"], kb_o["id"]])
    eff2 = await svc.replace_open_kbs(
        user_id=uid, key_id=k2["id"], kb_ids=[kb_o["id"], kb_g["id"]])
    assert eff1 == [kb_g["id"]]
    assert eff2 == [kb_o["id"]]

    keys = await svc.list_keys(user_id=uid, is_admin=False)
    assert len(keys) == 2
    by_dom = {k["domain"]: k for k in keys}
    assert by_dom["generic"]["open_kb_ids"] == [kb_g["id"]]
    assert by_dom["odn"]["open_kb_ids"] == [kb_o["id"]]
    assert by_dom["generic"]["domain_bound"] is True
    assert by_dom["odn"]["domain_bound"] is True

    # 验钥命中（明文→hash 路径）
    verified = await svc.verify_key(k1["key"])
    assert verified is not None and verified["domain"] == "generic"

    # 解绑一个域 → 该域钥匙 domain_bound=False（仍可 list，建钥才拦截）
    await kbdb.set_user_domains(user_id=uid, domains=["generic"])
    keys = await svc.list_keys(user_id=uid, is_admin=False)
    by_dom = {k["domain"]: k for k in keys}
    assert by_dom["generic"]["domain_bound"] is True
    assert by_dom["odn"]["domain_bound"] is False
    with pytest.raises(KeyDomainNotBound):
        await svc.create_key(user_id=uid, name=f"o2-{s}", domain="odn",
                             is_admin=False)

    # 轮换 + 吊销收尾：旧明文立即失效
    rotated = await svc.rotate_key(user_id=uid, key_id=k1["id"])
    assert await svc.verify_key(k1["key"]) is None
    assert (await svc.verify_key(rotated["key"]))["key_id"] == k1["id"]
    await svc.revoke_key(user_id=uid, key_id=k2["id"])
    assert await svc.verify_key(k2["key"]) is None
    with pytest.raises(KeyRevoked):
        await svc.rotate_key(user_id=uid, key_id=k2["id"])
