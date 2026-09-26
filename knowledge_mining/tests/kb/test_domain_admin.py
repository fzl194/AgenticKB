"""域管理员 RBAC：域授权、域用户管理与 KB 全管理权限。"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.db import DomainMembershipConflict, KbDB
from knowledge_mining.mining.kb.routes.auth import router as auth_router
from knowledge_mining.tests.conftest import kb_headers

pytestmark = pytest.mark.asyncio


def _name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _client(async_pool) -> AsyncClient:
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig

    app = FastAPI()
    app.state.pg_pool = async_pool
    app.state.db_config = MiningDbConfig()
    app.include_router(auth_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_domain_grants_default_member_and_preserve_role(async_pool):
    db = KbDB(async_pool)
    user = await db.create_user(username=_name("grant"), site_role="member")
    await db.set_domain_grants(user_id=user["id"], grants=[
        {"domain": "generic", "domain_role": "admin"},
        {"domain": "odn", "domain_role": "member"},
    ])
    assert await db.list_domain_grants(user_id=user["id"]) == [
        {"domain": "generic", "domain_role": "admin"},
        {"domain": "odn", "domain_role": "member"},
    ]
    # 旧入口只更新域集合；保留仍存在域的角色，不得把 admin 静默降级。
    await db.set_user_domains(user_id=user["id"], domains=["generic"])
    assert await db.list_domain_grants(user_id=user["id"]) == [
        {"domain": "generic", "domain_role": "admin"},
    ]


async def test_domain_admin_manages_every_kb_only_in_own_domain(async_pool):
    db = KbDB(async_pool)
    domain_admin = await db.create_user(username=_name("domain_admin"), site_role="member")
    owner = await db.create_user(username=_name("owner"), site_role="member")
    await db.set_domain_grants(user_id=domain_admin["id"], grants=[
        {"domain": "generic", "domain_role": "admin"},
        {"domain": "odn", "domain_role": "member"},
    ])
    own_domain_kb = await db.create_kb(
        domain="generic", name=_name("private"), owner_id=owner["id"], visibility="private",
    )
    other_domain_kb = await db.create_kb(
        domain="odn", name=_name("private"), owner_id=owner["id"], visibility="private",
    )
    assert await db.is_visible(kb_id=own_domain_kb["id"], user_id=domain_admin["id"])
    assert await db.can_write(kb_id=own_domain_kb["id"], user_id=domain_admin["id"])
    assert await db.can_restore(kb_id=own_domain_kb["id"], user_id=domain_admin["id"])
    assert not await db.is_visible(kb_id=other_domain_kb["id"], user_id=domain_admin["id"])
    assert not await db.can_write(kb_id=other_domain_kb["id"], user_id=domain_admin["id"])
    assert not await db.can_restore(kb_id=other_domain_kb["id"], user_id=domain_admin["id"])
    visible = await db.list_visible(user_id=domain_admin["id"], domain="generic")
    row = next(item for item in visible if item["id"] == own_domain_kb["id"])
    assert row["my_role"] == "domain_admin"


async def test_unbind_domain_rejects_owner_then_removes_member_edges(async_pool):
    db = KbDB(async_pool)
    target = await db.create_user(username=_name("remove"), site_role="member")
    other = await db.create_user(username=_name("other"), site_role="member")
    await db.bind_domain(user_id=target["id"], domain="generic")
    owned = await db.create_kb(domain="generic", name=_name("owned"), owner_id=target["id"])
    with pytest.raises(DomainMembershipConflict) as exc_info:
        await db.unbind_domain(user_id=target["id"], domain="generic")
    assert exc_info.value.code == "domain_has_owned_kbs"
    async with async_pool.connection() as conn:
        await conn.execute(
            "UPDATE knowledge_bases SET owner_id = %s WHERE id = %s",
            (other["id"], owned["id"]),
        )
    await db.add_member(kb_id=owned["id"], user_id=target["id"], role="editor")
    await db.unbind_domain(user_id=target["id"], domain="generic")
    assert await db.list_domain_grants(user_id=target["id"]) == []
    assert not any(m["user_id"] == target["id"] for m in await db.list_members(owned["id"]))


async def test_site_admin_assigns_roles_and_domain_admin_manages_members(async_pool):
    db = KbDB(async_pool)
    site_admin = await db.create_user(username=_name("root"), password_hash="x", site_role="admin")
    domain_admin = await db.create_user(
        username=_name("da"), password_hash="test-password-hash", site_role="member",
    )
    ordinary = await db.create_user(username=_name("ordinary"), site_role="member")
    async with await _client(async_pool) as client:
        response = await client.put(
            f"/api/kb/admin/users/{domain_admin['id']}/domain-grants",
            json={"grants": [{"domain": "generic", "domain_role": "admin"}]},
            headers=kb_headers(site_admin["username"]),
        )
        assert response.status_code == 200, response.text
        assert response.json()["domain_grants"] == [{"domain": "generic", "domain_role": "admin"}]
        response = await client.post(
            "/api/kb/domains/generic/users",
            json={"username": ordinary["username"]},
            headers=kb_headers(domain_admin["username"]),
        )
        assert response.status_code == 201, response.text
        assert response.json()["domain_role"] == "member"
        response = await client.get(
            "/api/kb/domains/generic/users", headers=kb_headers(domain_admin["username"]),
        )
        assert response.status_code == 200, response.text
        assert {u["id"] for u in response.json()["users"]} >= {domain_admin["id"], ordinary["id"]}
        # 域管理员不能删除另一个域管理员，也不能越域管理。
        response = await client.delete(
            f"/api/kb/domains/generic/users/{domain_admin['id']}",
            headers=kb_headers(domain_admin["username"]),
        )
        assert response.status_code == 403
        response = await client.get(
            "/api/kb/domains/odn/users", headers=kb_headers(domain_admin["username"]),
        )
        assert response.status_code == 403


async def test_domain_access_endpoint_exposes_scoped_capabilities(async_pool):
    db = KbDB(async_pool)
    domain_admin = await db.create_user(username=_name("caps"), site_role="member")
    await db.set_domain_grants(user_id=domain_admin["id"], grants=[
        {"domain": "generic", "domain_role": "admin"},
        {"domain": "odn", "domain_role": "member"},
    ])
    async with await _client(async_pool) as client:
        response = await client.get(
            "/api/kb/domain-access/me", headers=kb_headers(domain_admin["username"]),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["site_role"] == "member"
        assert body["capabilities_by_domain"] == {
            "generic": ["domain.kbs.manage", "domain.users.manage"],
            "odn": [],
        }


async def test_historical_owner_or_member_edge_cannot_bypass_domain_membership(async_pool):
    """域关系是外层门禁；历史脏 owner/member 边不能继续越域授权。"""
    db = KbDB(async_pool)
    owner = await db.create_user(username=_name("orphan_owner"), site_role="member")
    editor = await db.create_user(username=_name("orphan_editor"), site_role="member")
    await db.bind_domain(user_id=owner["id"], domain="generic")
    await db.bind_domain(user_id=editor["id"], domain="generic")
    kb = await db.create_kb(
        domain="generic", name=_name("hard_boundary"), owner_id=owner["id"],
    )
    await db.add_member(kb_id=kb["id"], user_id=editor["id"], role="editor")
    async with async_pool.connection() as conn:
        await conn.execute(
            "DELETE FROM user_domains WHERE user_id IN (%s, %s) AND domain = %s",
            (owner["id"], editor["id"], "generic"),
        )

    assert await db.is_visible(kb_id=kb["id"], user_id=owner["id"]) is False
    assert await db.can_write(kb_id=kb["id"], user_id=editor["id"]) is False
