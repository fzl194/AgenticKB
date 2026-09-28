from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import UserDeletionConflict
from knowledge_mining.mining.kb.services.kb_service import (
    Forbidden,
    KbService,
    NotFound,
)


class _TransferDb:
    def __init__(
        self, *, allowed: bool = True, exists: bool = True,
        transfer_error: str | None = None,
    ) -> None:
        self.allowed = allowed
        self.exists = exists
        self.transfer_error = transfer_error
        self.calls: list[dict] = []

    async def get_kb(self, kb_id: str):
        if not self.exists:
            return None
        return {"id": kb_id, "domain": "generic", "owner_id": "old-owner"}

    async def can_restore(self, *, kb_id: str, user_id: str) -> bool:
        return self.allowed

    async def transfer_kb_owner(self, **kwargs):
        self.calls.append(kwargs)
        if self.transfer_error:
            raise UserDeletionConflict(self.transfer_error)
        return {"id": kwargs["kb_id"], "owner_id": kwargs["new_owner_id"]}

    async def list_owner_candidates(self, *, kb_id: str, q: str | None, limit: int):
        return [{"id": "new", "username": "new-owner", "display_name": "New Owner"}]


@pytest.mark.asyncio
async def test_transfer_owner_reuses_lifecycle_permission_and_one_db_operation() -> None:
    denied = _TransferDb(allowed=False)
    with pytest.raises(Forbidden):
        await KbService(denied).transfer_owner(  # type: ignore[arg-type]
            kb_id="kb1", actor_id="editor", new_owner_id="new", keep_old_as_editor=False,
        )
    assert denied.calls == []

    missing = _TransferDb(exists=False)
    with pytest.raises(NotFound):
        await KbService(missing).transfer_owner(  # type: ignore[arg-type]
            kb_id="kb1", actor_id="admin", new_owner_id="new", keep_old_as_editor=False,
        )

    db = _TransferDb()
    result = await KbService(db).transfer_owner(  # type: ignore[arg-type]
        kb_id="kb1", actor_id="owner", new_owner_id="new", keep_old_as_editor=True,
    )
    assert result["owner_id"] == "new"
    assert db.calls == [{
        "kb_id": "kb1", "actor_id": "owner", "new_owner_id": "new",
        "keep_old_as_editor": True,
    }]


@pytest.mark.asyncio
async def test_owner_candidate_search_uses_same_lifecycle_permission() -> None:
    denied = _TransferDb(allowed=False)
    with pytest.raises(Forbidden):
        await KbService(denied).list_owner_candidates(  # type: ignore[arg-type]
            kb_id="kb1", actor_id="editor", q=None, limit=20,
        )

    db = _TransferDb()
    rows = await KbService(db).list_owner_candidates(  # type: ignore[arg-type]
        kb_id="kb1", actor_id="owner", q="new", limit=20,
    )
    assert rows == [{"id": "new", "username": "new-owner", "display_name": "New Owner"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        ("owner_transfer_actor_forbidden", Forbidden),
        ("owner_target_not_found", NotFound),
        ("owner_target_inactive", Forbidden),
        ("owner_target_not_domain_bound", Forbidden),
    ],
)
async def test_transfer_owner_maps_transaction_failures_to_stable_errors(
    code: str, error_type: type[Exception],
) -> None:
    db = _TransferDb(transfer_error=code)
    with pytest.raises(error_type, match=code):
        await KbService(db).transfer_owner(  # type: ignore[arg-type]
            kb_id="kb1", actor_id="owner", new_owner_id="new",
            keep_old_as_editor=False,
        )
