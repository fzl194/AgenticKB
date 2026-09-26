"""Pure policy tests for scoped administrator grants."""

import pytest
from fastapi import HTTPException

from knowledge_mining.mining.kb.routes.auth import require_domain_admin_credential


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
