"""UserService 业务层测试。"""
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


@pytest.mark.asyncio
async def test_create_user_hashes_password(svc):
    u = await svc.create_user(username="alice", password="pw123456", site_role="member", display_name="Alice")
    assert u["site_role"] == "member"
    assert verify_password("pw123456", u["password_hash"])


@pytest.mark.asyncio
async def test_create_user_duplicate(svc):
    await svc.create_user(username="alice", password="pw123456", site_role="member")
    with pytest.raises(DuplicateUser):
        await svc.create_user(username="alice", password="pw123456", site_role="member")


@pytest.mark.asyncio
async def test_create_user_bad_role(svc):
    with pytest.raises(InvalidRole):
        await svc.create_user(username="x", password="pw123456", site_role="superuser")


@pytest.mark.asyncio
async def test_create_user_short_password(svc):
    with pytest.raises(UserError):
        await svc.create_user(username="x", password="short", site_role="member")


@pytest.mark.asyncio
async def test_reset_password(svc):
    u = await svc.create_user(username="bob", password="old12345", site_role="member")
    await svc.reset_password(u["id"], "new12345")
    row = await svc._db.get_user(u["id"])
    assert verify_password("new12345", row["password_hash"])
    assert not verify_password("old12345", row["password_hash"])


@pytest.mark.asyncio
async def test_change_own_password_verifies_old(svc):
    u = await svc.create_user(username="carol", password="old12345", site_role="member")
    with pytest.raises(WrongPassword):
        await svc.change_own_password(user_id=u["id"], old="wrong", new="new12345")
    await svc.change_own_password(user_id=u["id"], old="old12345", new="new12345")
    assert verify_password("new12345", (await svc._db.get_user(u["id"]))["password_hash"])


@pytest.mark.asyncio
async def test_verify_credentials(svc):
    await svc.create_user(username="dave", password="pw123456", site_role="admin", display_name="Dave")
    ok = await svc.verify_credentials(username="dave", password="pw123456")
    assert ok is not None and ok["site_role"] == "admin"
    assert await svc.verify_credentials(username="dave", password="wrong") is None
    assert await svc.verify_credentials(username="nobody", password="x") is None


@pytest.mark.asyncio
async def test_verify_credentials_disabled_user(svc):
    u = await svc.create_user(username="dis", password="pw123456", site_role="member")
    await svc.update_user(user_id=u["id"], status="disabled")
    assert await svc.verify_credentials(username="dis", password="pw123456") is None
