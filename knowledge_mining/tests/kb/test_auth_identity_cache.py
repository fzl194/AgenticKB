"""批次3-问题1：每请求身份确认（upsert）缓存 + 禁用即时生效。

实测（P07-S2 测试窗口）：pool_max=10 下慢查询占满连接，认证 upsert 排队
30s 超时（PoolTimeout）→ 全站 401/500。修复：身份确认结果短 TTL 缓存，
命中不触库；账号禁用仍即时生效（安全语义不变）。
"""
from __future__ import annotations

import time

import pytest

from knowledge_mining.mining.kb.auth import IdentityCache

pytestmark = pytest.mark.asyncio


class _FakeDb:
    def __init__(self, user: dict | None = None):
        self.user = user or {"id": "u1", "username": "alice",
                             "status": "active", "site_role": "admin"}
        self.calls = 0

    async def upsert_user_by_username(self, username, *, display_name=None):
        self.calls += 1
        return dict(self.user)


def _cache(db, ttl=60.0):
    return IdentityCache(ttl_seconds=ttl)


async def test_cache_hits_do_not_touch_db():
    db = _FakeDb()
    cache = _cache(db)
    u1 = await cache.get_user(db, "alice")
    u2 = await cache.get_user(db, "alice")
    assert u1 == u2 == {"id": "u1", "username": "alice",
                        "status": "active", "site_role": "admin"}
    assert db.calls == 1  # 第二次命中缓存


async def test_ttl_expiry_refetches():
    db = _FakeDb()
    cache = IdentityCache(ttl_seconds=0.0)  # 立即过期
    await cache.get_user(db, "alice")
    await cache.get_user(db, "alice")
    assert db.calls == 2


async def test_disable_invalidates_cache_immediately():
    """禁用即时生效：禁用端点（同进程）调 invalidate，下次认证触库拿到 disabled。"""
    db = _FakeDb()
    cache = _cache(db)
    await cache.get_user(db, "alice")  # 缓存 active
    db.user = {**db.user, "status": "disabled"}
    cache.invalidate("alice")  # 禁用端点的钩子
    user = await cache.get_user(db, "alice")
    assert user["status"] == "disabled"
    assert db.calls == 2


async def test_invalidate_clears_entry():
    db = _FakeDb()
    cache = _cache(db)
    await cache.get_user(db, "alice")
    cache.invalidate("alice")
    await cache.get_user(db, "alice")
    assert db.calls == 2


async def test_cache_is_bounded():
    """容量有界：防长期运行用户枚举无界增长。"""
    db = _FakeDb()
    cache = IdentityCache(ttl_seconds=60.0, max_entries=3)
    for i in range(5):
        await cache.get_user(db, f"user{i}")
    assert cache.size() == 3


def test_ttl_default_is_minutes_not_hours():
    """默认 TTL 必须短（分钟级）——禁用/改名等管理操作的生效窗口。"""
    cache = IdentityCache()
    assert 30 <= cache.ttl_seconds <= 300
