from __future__ import annotations

import asyncio
import uuid

import pytest

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.user_service import (
    UserError,
    UserOwnsKnowledgeBases,
    UserService,
)
from knowledge_mining.mining.kb.services.kb_service import KbService
from knowledge_mining.mining.kb.services.mcp_key_service import McpKeyService


pytestmark = pytest.mark.asyncio


def _name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def test_user_clearance_removes_permissions_keys_and_restores_same_identity(async_pool):
    db = KbDB(async_pool)
    svc = UserService(db)
    admin = await db.create_user(
        username=_name("lifecycle_admin"), password_hash="hash", site_role="admin",
    )
    owner = await db.create_user(username=_name("lifecycle_owner"), site_role="member")
    target = await db.create_user(username=_name("lifecycle_target"), site_role="member")
    await db.bind_domain(user_id=owner["id"], domain="generic")
    await db.bind_domain(user_id=target["id"], domain="generic")
    kb = await db.create_kb(domain="generic", name=_name("lifecycle_kb"), owner_id=owner["id"])
    await db.add_member(kb_id=kb["id"], user_id=target["id"], role="viewer")
    await db.create_mcp_key(
        user_id=target["id"], name=_name("lifecycle_key"), domain="generic",
        key_hash=uuid.uuid4().hex, key_prefix="kbm_test", key_id=uuid.uuid4().hex,
    )

    deleted = await svc.delete_user(
        user_id=target["id"], actor_id=admin["id"],
        confirm_username=target["username"],
    )
    assert deleted["id"] == target["id"]
    assert deleted["deleted_at"] is not None
    assert await db.list_domain_grants(user_id=target["id"]) == []
    assert not any(row["user_id"] == target["id"] for row in await db.list_members(kb["id"]))
    assert await db.list_mcp_keys(user_id=target["id"]) == []

    restored = await svc.restore_user(user_id=target["id"])
    assert restored["id"] == target["id"]
    assert restored["status"] == "disabled"
    assert restored["site_role"] == "member"
    assert restored["deleted_at"] is None


async def test_user_clearance_blocks_any_retained_owned_kb(async_pool):
    db = KbDB(async_pool)
    svc = UserService(db)
    admin = await db.create_user(
        username=_name("owner_block_admin"), password_hash="hash", site_role="admin",
    )
    target = await db.create_user(username=_name("owner_block_target"), site_role="member")
    await db.bind_domain(user_id=target["id"], domain="generic")
    kb = await db.create_kb(domain="generic", name=_name("owner_block_kb"), owner_id=target["id"])
    await db.soft_delete(kb["id"])

    with pytest.raises(UserOwnsKnowledgeBases) as exc_info:
        await svc.delete_user(
            user_id=target["id"], actor_id=admin["id"],
            confirm_username=target["username"],
        )
    assert {row["id"] for row in exc_info.value.knowledge_bases} == {kb["id"]}


async def test_concurrent_admin_demotion_never_removes_all_active_admins(async_pool):
    db = KbDB(async_pool)
    first = await db.create_user(
        username=_name("concurrent_admin_a"), password_hash="hash", site_role="admin",
    )
    second = await db.create_user(
        username=_name("concurrent_admin_b"), password_hash="hash", site_role="admin",
    )
    disabled_admin_ids: list[str] = []
    async with async_pool.connection() as conn:
        cur = await conn.execute(
            """UPDATE kb_users SET status = 'disabled'
               WHERE site_role = 'admin' AND status = 'active'
                 AND deleted_at IS NULL AND id NOT IN (%s, %s)
               RETURNING id""",
            (first["id"], second["id"]),
        )
        disabled_admin_ids = [str(row["id"]) for row in await cur.fetchall()]

    arrived = 0
    both_counted = asyncio.Event()

    class _BarrierDb:
        def __getattr__(self, name):
            return getattr(db, name)

        async def count_active_admins(self) -> int:
            nonlocal arrived
            value = await db.count_active_admins()
            arrived += 1
            if arrived == 2:
                both_counted.set()
            await both_counted.wait()
            return value

    try:
        svc = UserService(_BarrierDb())  # type: ignore[arg-type]
        results = await asyncio.gather(
            svc.update_user(
                user_id=first["id"], actor_id=second["id"], site_role="member",
            ),
            svc.update_user(
                user_id=second["id"], actor_id=first["id"], site_role="member",
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, UserError) for result in results) == 1
        assert await db.count_active_admins() == 1
    finally:
        if disabled_admin_ids:
            async with async_pool.connection() as conn:
                await conn.execute(
                    "UPDATE kb_users SET status = 'active' WHERE id = ANY(%s)",
                    (disabled_admin_ids,),
                )


async def test_concurrent_clearance_and_owner_transfer_never_create_deleted_owner(async_pool):
    db = KbDB(async_pool)
    user_svc = UserService(db)
    kb_svc = KbService(db)
    admin = await db.create_user(
        username=_name("transfer_admin"), password_hash="hash", site_role="admin",
    )
    source = await db.create_user(username=_name("transfer_source"), site_role="member")
    target = await db.create_user(username=_name("transfer_target"), site_role="member")
    await db.bind_domain(user_id=source["id"], domain="generic")
    await db.bind_domain(user_id=target["id"], domain="generic")
    kb = await db.create_kb(domain="generic", name=_name("transfer_race"), owner_id=source["id"])

    target_locked = asyncio.Event()
    release_transfer = asyncio.Event()

    class _PausingTransferDb(KbDB):
        async def _lock_active_user_on_conn(self, conn, *, user_id: str) -> bool:
            result = await super()._lock_active_user_on_conn(conn, user_id=user_id)
            if user_id == target["id"]:
                target_locked.set()
                await release_transfer.wait()
            return result

    transfer_svc = KbService(_PausingTransferDb(async_pool))
    transfer_task = asyncio.create_task(
        transfer_svc.transfer_owner(
            kb_id=kb["id"], actor_id=source["id"], new_owner_id=target["id"],
            keep_old_as_editor=False,
        )
    )
    await target_locked.wait()
    delete_task = asyncio.create_task(user_svc.delete_user(
        user_id=target["id"], actor_id=admin["id"],
        confirm_username=target["username"],
    ))
    release_transfer.set()
    transfer_result, delete_result = await asyncio.gather(
        transfer_task, delete_task, return_exceptions=True,
    )

    target_after = await db.get_user(target["id"])
    kb_after = await db.get_kb(kb["id"])
    assert target_after is not None and kb_after is not None
    assert not isinstance(transfer_result, Exception)
    assert isinstance(delete_result, UserOwnsKnowledgeBases)
    assert target_after["deleted_at"] is None
    assert kb_after["owner_id"] == target["id"]


async def test_concurrent_unbind_and_key_create_leave_no_active_unbound_key(async_pool):
    db = KbDB(async_pool)
    user = await db.create_user(username=_name("unbind_key"), site_role="member")
    await db.bind_domain(user_id=user["id"], domain="generic")
    await db.bind_domain(user_id=user["id"], domain="odn")

    unbind_entered = asyncio.Event()
    release_unbind = asyncio.Event()

    class _PausingUnbindDb(KbDB):
        async def _unbind_domain_on_conn(self, conn, *, user_id: str, domain: str) -> None:
            unbind_entered.set()
            await release_unbind.wait()
            await super()._unbind_domain_on_conn(conn, user_id=user_id, domain=domain)

    unbind_db = _PausingUnbindDb(async_pool)
    unbind_task = asyncio.create_task(
        unbind_db.unbind_domain(user_id=user["id"], domain="generic")
    )
    await unbind_entered.wait()
    key_task = asyncio.create_task(
        McpKeyService(db).create_key(
            user_id=user["id"], name=_name("race_key"),
            domain="generic", is_admin=False,
        )
    )
    release_unbind.set()
    unbind_result, key_result = await asyncio.gather(
        unbind_task, key_task, return_exceptions=True,
    )

    grants = await db.list_domain_grants(user_id=user["id"])
    keys = await db.list_mcp_keys(user_id=user["id"])
    assert not isinstance(unbind_result, Exception)
    assert isinstance(key_result, Exception)
    assert "generic" not in {grant["domain"] for grant in grants}
    assert not any(
        key["domain"] == "generic" and key["status"] == "active"
        for key in keys
    )
