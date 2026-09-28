from __future__ import annotations

import inspect
from typing import Any

import pytest

from knowledge_mining.mining.kb.db import KbDB


class _Cursor:
    async def fetchall(self) -> list[dict[str, Any]]:
        return []


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, query: str, params: Any = None) -> _Cursor:
        self.calls.append((" ".join(query.split()), params))
        return _Cursor()


class _ConnectionContext:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection_instance = connection

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(self.connection_instance)


def test_mcp_hash_lookup_guards_deleted_user_in_all_three_queries() -> None:
    source = inspect.getsource(KbDB.find_mcp_key_by_hash)
    assert source.count("u.deleted_at IS NULL") == 3


@pytest.mark.asyncio
async def test_candidate_searches_escape_ilike_metacharacters() -> None:
    connection = _Connection()
    db = KbDB(_Pool(connection))
    query = r"a%\_b"

    await db.list_domain_user_candidates(domain="generic", q=query, limit=20)
    await db.list_owner_candidates(kb_id="kb1", q=query, limit=20)
    await db.list_member_candidates(kb_id="kb1", q=query)

    domain_sql, domain_params = connection.calls[0]
    owner_sql, owner_params = connection.calls[1]
    member_sql, member_params = connection.calls[2]
    assert domain_params["pattern"] == r"%a\%\\\_b%"
    assert owner_params["pattern"] == r"%a\%\\\_b%"
    assert member_params[-1] == r"a\%\\\_b%"
    assert domain_sql.count("ESCAPE '\\'") == 2
    assert owner_sql.count("ESCAPE '\\'") == 2
    assert member_sql.count("ESCAPE '\\'") == 1
