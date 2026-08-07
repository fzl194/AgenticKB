"""UserService 业务层测试（工号登录模型）。"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.security import verify_password
from knowledge_mining.mining.kb.services.user_service import (
    DuplicateUser, InvalidRole, UserError, UserService, WrongPassword,
)


@pytest.fixture
def svc(async_pool):
    return UserService(KbDB(async_pool))


# ── 创建账号 ──

@pytest.mark.asyncio
async def test_create_admin_hashes_password(svc):
    u = await svc.create_user(username="root", password="pw123456", site_role="admin", display_name="Root")
    assert u["site_role"] == "admin"
    assert verify_password("pw123456", u["password_hash"])


@pytest.mark.asyncio
async def test_create_member_has_no_password(svc):
    """工号 member 无密码（白名单 = 表里有此行即信任）。"""
    u = await svc.create_user(username="alice", site_role="member", display_name="Alice")
    assert u["site_role"] == "member"
    assert u["password_hash"] is None


@pytest.mark.asyncio
async def test_create_admin_requires_password(svc):
    with pytest.raises(UserError):
        await svc.create_user(username="root", site_role="admin")  # 无密码


@pytest.mark.asyncio
async def test_create_user_duplicate(svc):
    await svc.create_user(username="alice", site_role="member")
    with pytest.raises(DuplicateUser):
        await svc.create_user(username="alice", site_role="member")


@pytest.mark.asyncio
async def test_create_user_bad_role(svc):
    with pytest.raises(InvalidRole):
        await svc.create_user(username="x", site_role="superuser")


@pytest.mark.asyncio
async def test_create_admin_short_password(svc):
    with pytest.raises(UserError):
        await svc.create_user(username="root", password="short", site_role="admin")


# ── 升 admin 必须有密码 ──

@pytest.mark.asyncio
async def test_promote_passwordless_member_to_admin_rejected(svc):
    m = await svc.create_user(username="alice", site_role="member")
    with pytest.raises(UserError):
        await svc.update_user(user_id=m["id"], actor_id="someadmin", site_role="admin")


@pytest.mark.asyncio
async def test_promote_to_admin_ok_after_password_set(svc):
    admin = await svc.create_user(username="root", password="pw123456", site_role="admin")
    m = await svc.create_user(username="alice", site_role="member")
    await svc.reset_password(m["id"], "newpw123")
    updated = await svc.update_user(user_id=m["id"], actor_id=admin["id"], site_role="admin")
    assert updated["site_role"] == "admin"


# ── self / last-admin 守卫（沿用）──

@pytest.mark.asyncio
async def test_cannot_disable_self(svc):
    admin = await svc.create_user(username="root", password="pw123456", site_role="admin")
    with pytest.raises(UserError):
        await svc.update_user(user_id=admin["id"], actor_id=admin["id"], status="disabled")


@pytest.mark.asyncio
async def test_cannot_demote_last_admin(svc):
    admin = await svc.create_user(username="root", password="pw123456", site_role="admin")
    other = await svc.create_user(username="other", site_role="member")
    with pytest.raises(UserError):
        await svc.update_user(user_id=admin["id"], actor_id=other["id"], site_role="member")


# ── identify ──

@pytest.mark.asyncio
async def test_identify_modes(svc):
    await svc.create_user(username="root", password="pw123456", site_role="admin")
    await svc.create_user(username="alice", site_role="member")
    assert (await svc.identify("root"))["mode"] == "password"
    assert (await svc.identify("alice"))["mode"] == "member"
    assert (await svc.identify("nobody"))["mode"] == "not_found"


@pytest.mark.asyncio
async def test_identify_disabled_user_not_found(svc):
    a1 = await svc.create_user(username="root", password="pw123456", site_role="admin")
    a2 = await svc.create_user(username="a2", password="pw123456", site_role="admin")
    # a2 禁用 a1（不能 self-disable）
    await svc.update_user(user_id=a1["id"], actor_id=a2["id"], status="disabled")
    assert (await svc.identify("root"))["mode"] == "not_found"


# ── verify_credentials（新逻辑：admin 验密 / member 走 SSO 口子）──

@pytest.mark.asyncio
async def test_verify_admin_password(svc):
    await svc.create_user(username="root", password="pw123456", site_role="admin")
    ok = await svc.verify_credentials(username="root", password="pw123456")
    assert ok is not None and ok["site_role"] == "admin"
    assert await svc.verify_credentials(username="root", password="wrong") is None


@pytest.mark.asyncio
async def test_verify_member_no_password_passes(svc):
    """工号 member 无密码 → 直接通过（SSO 口子现恒 True）。"""
    await svc.create_user(username="alice", site_role="member")
    ok = await svc.verify_credentials(username="alice")  # 不传密码
    assert ok is not None and ok["site_role"] == "member"


@pytest.mark.asyncio
async def test_verify_not_found_returns_none(svc):
    assert await svc.verify_credentials(username="nobody", password="x") is None


@pytest.mark.asyncio
async def test_verify_intranet_auth_hook_returns_true(svc):
    """【SSO 口子】当前恒 True。"""
    assert await svc.verify_intranet_auth("anyone") is True


# ── reset / change password ──

@pytest.mark.asyncio
async def test_reset_password(svc):
    u = await svc.create_user(username="root", password="old12345", site_role="admin")
    await svc.reset_password(u["id"], "new12345")
    row = await svc._db.get_user(u["id"])
    assert verify_password("new12345", row["password_hash"])
    assert not verify_password("old12345", row["password_hash"])


@pytest.mark.asyncio
async def test_change_own_password_verifies_old(svc):
    u = await svc.create_user(username="root", password="old12345", site_role="admin")
    with pytest.raises(WrongPassword):
        await svc.change_own_password(user_id=u["id"], old="wrong", new="new12345")
    await svc.change_own_password(user_id=u["id"], old="old12345", new="new12345")
    assert verify_password("new12345", (await svc._db.get_user(u["id"]))["password_hash"])
