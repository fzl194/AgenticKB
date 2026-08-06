"""KbDB 新增用户管理方法 + upsert 不变量测试（§5.3）。"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB


@pytest.mark.asyncio
async def test_upsert_user_returns_site_role(async_pool):
    db = KbDB(async_pool)
    u = await db.upsert_user_by_username("alice")
    assert u["username"] == "alice"
    assert u["site_role"] == "member"  # 新列在返回里
    assert "id" in u and "status" in u


@pytest.mark.asyncio
async def test_upsert_does_not_overwrite_admin_role_or_password(async_pool):
    """§5.3 不变量：对已存在 admin 用户名重复 upsert，site_role/password_hash 不被清。"""
    db = KbDB(async_pool)
    await db.create_user(username="admin", password_hash="$algo$1$AA$BB", site_role="admin")
    # 日常 KB 流量再次 upsert 同名（display_name 不同）
    await db.upsert_user_by_username("admin", display_name="日常名")
    got = await db.get_user_by_username("admin")
    assert got["site_role"] == "admin"               # 未被降级
    assert got["password_hash"] == "$algo$1$AA$BB"   # 未被清空
    assert got["display_name"] == "日常名"            # display_name 仍可更新


@pytest.mark.asyncio
async def test_user_crud(async_pool):
    db = KbDB(async_pool)
    u = await db.create_user(username="bob", password_hash="h1", site_role="member", display_name="Bob")
    assert u["site_role"] == "member"
    assert (await db.list_users()) and any(x["username"] == "bob" for x in await db.list_users())
    await db.update_user(u["id"], site_role="admin")
    assert (await db.get_user_by_username("bob"))["site_role"] == "admin"
    await db.update_user(u["id"], status="disabled")
    assert (await db.get_user_by_username("bob"))["status"] == "disabled"
    await db.set_password_hash(u["id"], "h2")
    assert (await db.get_user(u["id"]))["password_hash"] == "h2"


@pytest.mark.asyncio
async def test_has_admin(async_pool):
    db = KbDB(async_pool)
    assert await db.has_admin() is False
    # 只建 member（无密码）→ 仍无 admin
    await db.create_user(username="m1", password_hash="h", site_role="member")
    assert await db.has_admin() is False
    # admin 但无密码 → 不可登录，不算
    await db.create_user(username="a_nopw", password_hash=None, site_role="admin")
    assert await db.has_admin() is False
    # admin 有密码 → True
    await db.create_user(username="a_pw", password_hash="h", site_role="admin")
    assert await db.has_admin() is True
