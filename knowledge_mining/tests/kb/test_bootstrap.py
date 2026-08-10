"""bootstrap 播种首 admin（幂等 + 不变量）。"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb import bootstrap
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.security import verify_password


@pytest.mark.asyncio
async def test_seeds_admin_when_none(async_pool):
    await bootstrap.seed_initial_admin(async_pool, admin_password="init-pass")
    admin = await KbDB(async_pool).get_user_by_username("admin")
    assert admin is not None
    assert admin["site_role"] == "admin"
    assert verify_password("init-pass", admin["password_hash"])


@pytest.mark.asyncio
async def test_idempotent_preserves_existing_admin(async_pool):
    """已有可登录 admin → 二次启动不改其 password_hash/site_role。"""
    await bootstrap.seed_initial_admin(async_pool, admin_password="first")
    original_hash = (await KbDB(async_pool).get_user_by_username("admin"))["password_hash"]
    # 二次播种用不同密码
    await bootstrap.seed_initial_admin(async_pool, admin_password="different")
    admin2 = await KbDB(async_pool).get_user_by_username("admin")
    assert admin2["password_hash"] == original_hash  # 未被覆盖
    assert admin2["site_role"] == "admin"
    assert verify_password("first", admin2["password_hash"])  # 原密码仍有效


@pytest.mark.asyncio
async def test_seeds_when_only_members_exist(async_pool):
    db = KbDB(async_pool)
    await db.create_user(username="member1", password_hash="h", site_role="member")
    await bootstrap.seed_initial_admin(async_pool, admin_password="p")
    admin = await db.get_user_by_username("admin")
    assert admin is not None and admin["site_role"] == "admin"


@pytest.mark.asyncio
async def test_promotes_existing_admin_username_row(async_pool):
    """Phase 1 已 upsert 出来的无密码 admin 行 → 提权并设密。"""
    db = KbDB(async_pool)
    await db.upsert_user_by_username("admin")  # 模拟 Phase 1 流量造出的 admin 行（无密码、member）
    assert (await db.get_user_by_username("admin"))["password_hash"] is None
    await bootstrap.seed_initial_admin(async_pool, admin_password="p")
    admin = await db.get_user_by_username("admin")
    assert admin["site_role"] == "admin"
    assert verify_password("p", admin["password_hash"])


@pytest.mark.asyncio
async def test_empty_password_skips(async_pool):
    await bootstrap.seed_initial_admin(async_pool, admin_password="")
    assert await KbDB(async_pool).has_admin() is False


@pytest.mark.asyncio
async def test_placeholder_password_skips(async_pool):
    """样板占位符（仓库公开）不能用来播种首 admin。"""
    await bootstrap.seed_initial_admin(async_pool, admin_password="change-me-on-first-login")
    assert await KbDB(async_pool).has_admin() is False


@pytest.mark.asyncio
async def test_does_not_clobber_existing_admin_with_password(async_pool):
    """admin 用户名已存在且已设密码（有人配过）→ 不覆盖。"""
    db = KbDB(async_pool)
    await db.create_user(
        username="admin", password_hash="$algo$1$AA$BB", site_role="member",
    )  # 故意 member + 有密码
    await bootstrap.seed_initial_admin(async_pool, admin_password="realpass1")
    admin = await db.get_user_by_username("admin")
    assert admin["password_hash"] == "$algo$1$AA$BB"  # 未被覆盖

