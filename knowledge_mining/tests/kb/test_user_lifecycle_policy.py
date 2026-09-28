from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.services.user_service import (
    UserError,
    UserOwnsKnowledgeBases,
    UserService,
    UsernameReserved,
)
from knowledge_mining.mining.kb.routes.auth import CreateUserReq, create_user as create_user_route


class _LifecycleDb:
    def __init__(
        self, *, target: dict, owned: list[dict] | None = None,
        grants: list[dict[str, str]] | None = None,
        deletion_counts: dict[str, int] | None = None,
    ) -> None:
        self.target = target
        self.owned = owned or []
        self.deleted: list[tuple[str, str]] = []
        self.restored: list[str] = []
        self.demoted: list[tuple[str, str]] = []
        self.grants = grants or []
        self.deletion_counts = deletion_counts or {
            "domain_count": 0,
            "kb_member_count": 0,
            "mcp_key_count": 0,
        }

    async def get_user(self, user_id: str):
        return self.target if self.target.get("id") == user_id else None

    async def get_user_by_username(self, username: str):
        return self.target if self.target.get("username") == username else None

    async def count_active_admins(self) -> int:
        return 2

    async def list_owned_kbs_for_user(self, *, user_id: str):
        return self.owned

    async def get_user_deletion_counts(self, *, user_id: str):
        return dict(self.deletion_counts)

    async def delete_user_account(self, *, user_id: str, deleted_by_user_id: str):
        self.deleted.append((user_id, deleted_by_user_id))
        return {**self.target, "status": "disabled", "deleted_at": "now"}

    async def restore_user_account(self, *, user_id: str):
        self.restored.append(user_id)
        return {**self.target, "status": "disabled", "deleted_at": None}

    async def demote_admin_to_member(self, *, user_id: str, fallback_domain: str):
        self.demoted.append((user_id, fallback_domain))
        return {**self.target, "site_role": "member"}

    async def list_domain_grants(self, *, user_id: str):
        return self.grants


def _service(db: _LifecycleDb) -> UserService:
    return UserService(db)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_delete_user_rejects_self_and_owned_kbs() -> None:
    self_db = _LifecycleDb(target={
        "id": "u1", "username": "alice", "site_role": "member", "deleted_at": None,
    })
    with pytest.raises(UserError, match="不能删除自己的账号"):
        await _service(self_db).delete_user(
            user_id="u1", actor_id="u1", confirm_username="alice",
        )

    owner_db = _LifecycleDb(
        target={"id": "u1", "username": "alice", "site_role": "member", "deleted_at": None},
        owned=[{"id": "kb1", "name": "仍保留的库", "status": "deleted"}],
    )
    with pytest.raises(UserOwnsKnowledgeBases) as exc_info:
        await _service(owner_db).delete_user(
            user_id="u1", actor_id="admin", confirm_username="alice",
        )
    assert exc_info.value.knowledge_bases == owner_db.owned
    assert owner_db.deleted == []


@pytest.mark.asyncio
async def test_delete_and_restore_preserve_identity_without_permissions() -> None:
    target = {
        "id": "u1", "username": "alice", "site_role": "member", "deleted_at": None,
    }
    db = _LifecycleDb(target=target)
    deleted = await _service(db).delete_user(
        user_id="u1", actor_id="admin", confirm_username="alice",
    )
    assert deleted["id"] == "u1"
    assert deleted["status"] == "disabled"
    assert db.deleted == [("u1", "admin")]

    db.target = {**deleted, "deleted_at": "now"}
    restored = await _service(db).restore_user(user_id="u1")
    assert restored["id"] == "u1"
    assert restored["status"] == "disabled"
    assert restored["deleted_at"] is None
    assert db.restored == ["u1"]


@pytest.mark.asyncio
async def test_delete_user_requires_exact_username_confirmation() -> None:
    db = _LifecycleDb(target={
        "id": "u1", "username": "alice", "site_role": "member", "deleted_at": None,
    })
    with pytest.raises(UserError, match="confirm_username_mismatch"):
        await _service(db).delete_user(
            user_id="u1", actor_id="admin", confirm_username="ALICE",
        )
    assert db.deleted == []


@pytest.mark.asyncio
async def test_admin_demotion_uses_atomic_domain_binding_path() -> None:
    db = _LifecycleDb(target={
        "id": "u1", "username": "alice", "site_role": "admin",
        "status": "active", "deleted_at": None,
    })
    updated = await _service(db).update_user(
        user_id="u1", actor_id="other-admin", site_role="member",
    )
    assert updated["site_role"] == "member"
    assert len(db.demoted) == 1
    assert db.demoted[0][0] == "u1"


@pytest.mark.asyncio
async def test_deletion_preview_never_exposes_password_hash() -> None:
    db = _LifecycleDb(
        target={
            "id": "u1", "username": "alice", "display_name": "Alice",
            "site_role": "member", "status": "active", "deleted_at": None,
            "password_hash": "secret-hash", "deleted_by_user_id": None,
        },
        deletion_counts={
            "domain_count": 2,
            "kb_member_count": 3,
            "mcp_key_count": 4,
        },
    )
    preview = await _service(db).deletion_preview(user_id="u1")
    assert preview["user"] == {
        "id": "u1", "username": "alice", "display_name": "Alice",
        "status": "active", "site_role": "member", "deleted_at": None,
    }
    assert "password_hash" not in preview["user"]
    assert preview["domain_count"] == 2
    assert preview["kb_member_count"] == 3
    assert preview["mcp_key_count"] == 4


@pytest.mark.asyncio
async def test_deleted_username_is_reserved_for_identity_restore() -> None:
    db = _LifecycleDb(target={
        "id": "u1", "username": "alice", "site_role": "member",
        "status": "disabled", "deleted_at": "now",
    })
    with pytest.raises(UsernameReserved, match="username_reserved"):
        await _service(db).create_user(username="alice", site_role="member")


@pytest.mark.asyncio
async def test_restored_member_requires_domain_before_enable() -> None:
    db = _LifecycleDb(target={
        "id": "u1", "username": "alice", "site_role": "member",
        "status": "disabled", "deleted_at": None,
    })
    with pytest.raises(UserError, match="domain_grants_must_not_be_empty"):
        await _service(db).update_user(
            user_id="u1", actor_id="admin", status="active",
        )


class _CreateService:
    async def create_user(self, **_kwargs):
        return {
            "id": "u1", "username": "root", "display_name": "Root",
            "status": "active", "site_role": "admin", "created_at": "now",
            "password_hash": "pbkdf2-secret-hash",
        }


@pytest.mark.asyncio
async def test_create_user_response_never_exposes_password_hash() -> None:
    response = await create_user_route(
        CreateUserReq(username="root", password="password123", site_role="admin"),
        _admin={"id": "admin"},
        svc=_CreateService(),  # type: ignore[arg-type]
    )
    assert response["username"] == "root"
    assert "password_hash" not in response
