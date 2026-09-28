from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from knowledge_mining.mining.kb.services.user_import_service import (
    UserImportError,
    UserImportService,
)


class _ImportDb:
    def __init__(self) -> None:
        self.users = {
            "existing": {
                "id": "u-existing", "username": "existing", "status": "active",
                "site_role": "member", "deleted_at": None,
            },
            "disabled": {
                "id": "u-disabled", "username": "disabled", "status": "disabled",
                "site_role": "member", "deleted_at": None,
            },
        }
        self.applied: list[dict] = []

    async def get_user_by_username(self, username: str):
        return self.users.get(username)

    async def get_users_by_usernames(self, usernames: list[str]):
        return {
            username: self.users[username]
            for username in usernames
            if username in self.users
        }

    async def apply_user_import(self, *, new_users: list[dict], bindings: list[dict]):
        self.applied.append({"new_users": new_users, "bindings": bindings})


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_csv_and_xlsx_build_the_same_site_admin_plan() -> None:
    csv_data = (
        "username,display_name,domain\n"
        "new-user,New User,generic\n"
        "new-user,New User,odn\n"
        "existing,Existing,generic\n"
    ).encode()
    xlsx_data = _xlsx_bytes([
        ["username", "display_name", "domain"],
        ["new-user", "New User", "generic"],
        ["new-user", "New User", "odn"],
        ["existing", "Existing", "generic"],
    ])
    csv_plan = await UserImportService(_ImportDb()).execute(
        filename="users.csv", content=csv_data, dry_run=True,
    )
    xlsx_plan = await UserImportService(_ImportDb()).execute(
        filename="users.xlsx", content=xlsx_data, dry_run=True,
    )
    assert csv_plan == xlsx_plan
    assert csv_plan["new_users"] == [{
        "username": "new-user", "display_name": "New User",
        "domains": ["generic", "odn"],
    }]
    assert csv_plan["bindings"] == [{"user_id": "u-existing", "domain": "generic"}]


@pytest.mark.asyncio
async def test_import_dry_run_never_writes_and_errors_block_apply() -> None:
    db = _ImportDb()
    valid = b"username,display_name,domain\nnew-user,New User,generic\n"
    await UserImportService(db).execute(
        filename="users.csv", content=valid, dry_run=True,
    )
    assert db.applied == []

    invalid = b"username,display_name,domain\ndisabled,Disabled,generic\n"
    plan = await UserImportService(db).execute(
        filename="users.csv", content=invalid, dry_run=False,
    )
    assert plan["errors"][0]["code"] == "user_not_active"
    assert db.applied == []


@pytest.mark.asyncio
async def test_domain_admin_import_only_binds_existing_active_users() -> None:
    db = _ImportDb()
    content = b"username\nexisting\nunknown\n"
    plan = await UserImportService(db).execute(
        filename="members.csv", content=content, domain="generic", dry_run=True,
    )
    assert plan["bindings"] == [{"user_id": "u-existing", "domain": "generic"}]
    assert plan["errors"] == [{
        "row": 3, "username": "unknown", "code": "user_not_found",
    }]


@pytest.mark.asyncio
async def test_xlsx_formula_and_unsupported_files_are_rejected() -> None:
    formula = _xlsx_bytes([
        ["username", "display_name", "domain"],
        ["=HYPERLINK(\"https://bad\")", "Bad", "generic"],
    ])
    with pytest.raises(UserImportError, match="formula_not_allowed"):
        await UserImportService(_ImportDb()).execute(
            filename="users.xlsx", content=formula, dry_run=True,
        )
    with pytest.raises(UserImportError, match="unsupported_file_type"):
        await UserImportService(_ImportDb()).execute(
            filename="users.xls", content=b"legacy", dry_run=True,
        )


@pytest.mark.asyncio
async def test_domain_import_rejects_privilege_or_cross_domain_columns() -> None:
    content = b"username,domain,role\nexisting,odn,admin\n"
    with pytest.raises(UserImportError, match="unexpected_columns"):
        await UserImportService(_ImportDb()).execute(
            filename="members.csv", content=content,
            domain="generic", dry_run=True,
        )
