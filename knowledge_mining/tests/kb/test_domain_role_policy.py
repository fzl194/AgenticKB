"""Pure policy tests for scoped administrator grants."""

import pytest
from fastapi import HTTPException

from knowledge_mining.mining.kb.routes.auth import require_domain_admin_credential
from knowledge_mining.mining.kb.security import verify_password
from knowledge_mining.mining.kb.services.user_service import UserError, UserService


def test_domain_admin_grant_requires_password_until_sso_is_real() -> None:
    with pytest.raises(HTTPException) as exc:
        require_domain_admin_credential(
            {"username": "alice", "password_hash": None},
            [{"domain": "generic", "domain_role": "admin"}],
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "domain_admin_requires_password"


def test_domain_admin_grant_accepts_password_backed_account() -> None:
    require_domain_admin_credential(
        {"username": "alice", "password_hash": "pbkdf2_sha256$..."},
        [{"domain": "generic", "domain_role": "admin"}],
    )


def test_member_only_grants_do_not_require_password() -> None:
    require_domain_admin_credential(
        {"username": "alice", "password_hash": None},
        [{"domain": "generic", "domain_role": "member"}],
    )


class _GrantDb:
    def __init__(self, *, password_hash: str | None) -> None:
        self.target = {
            "id": "u1", "username": "alice", "site_role": "member",
            "password_hash": password_hash, "deleted_at": None,
        }
        self.saved: tuple[list[dict[str, str]], str | None] | None = None

    async def get_user(self, user_id: str):
        return self.target if user_id == "u1" else None

    async def set_domain_grants_with_password(
        self, *, user_id: str, grants: list[dict[str, str]], password_hash: str | None,
    ):
        self.saved = (grants, password_hash)
        return grants


@pytest.mark.asyncio
async def test_passwordless_domain_admin_grant_is_atomic_with_initial_password() -> None:
    missing = _GrantDb(password_hash=None)
    with pytest.raises(UserError, match="domain_admin_requires_password"):
        await UserService(missing).assign_domain_grants(  # type: ignore[arg-type]
            user_id="u1",
            grants=[{"domain": "generic", "domain_role": "admin"}],
        )
    assert missing.saved is None

    db = _GrantDb(password_hash=None)
    grants = [{"domain": "generic", "domain_role": "admin"}]
    result = await UserService(db).assign_domain_grants(  # type: ignore[arg-type]
        user_id="u1", grants=grants, initial_password="domainpw123",
    )
    assert result == grants
    assert db.saved is not None
    assert db.saved[1] != "domainpw123"
    assert verify_password("domainpw123", db.saved[1])
