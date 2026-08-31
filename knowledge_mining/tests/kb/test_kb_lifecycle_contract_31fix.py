"""31 号 Wave 1：KB 名称生命周期和默认 Workflow 契约。"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _Connection:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        if "INSERT INTO knowledge_bases" in sql:
            return _Cursor({
                "id": params["id"], "domain": params["dom"],
                "name": params["n"], "description": params["desc"],
                "owner_id": params["own"], "visibility": params["vis"],
                "status": "active", "created_at": params["t"],
                "updated_at": params["t"],
                "mining_workflow_id": params["wf"],
            })
        return _Cursor(None)


class _Pool:
    def __init__(self):
        self.conn = _Connection()

    @asynccontextmanager
    async def connection(self):
        yield self.conn


def test_domain_schema_includes_kb_lifecycle_migration() -> None:
    from knowledge_mining.mining.infra.pg_schema import domain_schema_paths

    path = next(
        path for path in domain_schema_paths()
        if path.name == "011_kb_lifecycle_names_and_default_workflow.sql"
    )
    ddl = path.read_text(encoding="utf-8").lower()
    assert "drop constraint if exists knowledge_bases_domain_name_key" in ddl
    assert "owner_id" in ddl and "where status = 'active'" in ddl
    assert "system-full-baseline" in ddl and "system-hybrid-assets" in ddl


@pytest.mark.asyncio
async def test_new_kb_uses_current_default_workflow() -> None:
    from knowledge_mining.mining.kb.db import KbDB
    from knowledge_mining.mining.workflow.presets import DEFAULT_WORKFLOW_ID

    pool = _Pool()
    kb = await KbDB(pool).create_kb(
        domain="odn", name="test", owner_id="user-1",
    )
    assert kb["mining_workflow_id"] == DEFAULT_WORKFLOW_ID
    insert_params = next(
        params for sql, params in pool.conn.calls
        if "INSERT INTO knowledge_bases" in sql
    )
    assert insert_params["wf"] == "system-hybrid-assets"


@pytest.mark.asyncio
async def test_service_normalizes_names_for_create_and_update() -> None:
    from knowledge_mining.mining.kb.services.kb_service import KbService

    class _Db:
        async def create_kb(self, **kwargs):
            return kwargs

        async def is_visible(self, **_kwargs):
            return True

        async def can_write(self, **_kwargs):
            return True

        async def update_kb(self, _kb_id, *, fields):
            return fields

    service = KbService(_Db())
    created = await service.create_kb(
        domain="odn", name="  test  ", owner_id="u1",
    )
    assert created["name"] == "test"
    updated = await service.update_kb(
        kb_id="kb1", actor_id="u1", fields={"name": "  renamed  "},
    )
    assert updated["name"] == "renamed"


def test_kb_name_rejects_blank_and_overlong_values() -> None:
    from knowledge_mining.mining.kb.services.kb_service import (
        InvalidName,
        normalize_kb_name,
    )

    with pytest.raises(InvalidName):
        normalize_kb_name("   ")
    with pytest.raises(InvalidName):
        normalize_kb_name("x" * 81)


@pytest.mark.asyncio
async def test_deleted_kb_owner_can_restore() -> None:
    from knowledge_mining.mining.kb.services.kb_service import KbService

    class _Db:
        async def get_kb(self, _kb_id, *, include_deleted=False):
            assert include_deleted is True
            return {"id": "kb1", "status": "deleted", "owner_id": "u1"}

        async def can_restore(self, *, kb_id, user_id):
            return kb_id == "kb1" and user_id == "u1"

        async def restore_kb(self, kb_id):
            return {"id": kb_id, "status": "active", "deleted_at": None}

    restored = await KbService(_Db()).restore_kb(
        kb_id="kb1", actor_id="u1",
    )
    assert restored["status"] == "active"


@pytest.mark.asyncio
async def test_editor_cannot_delete_entire_knowledge_base() -> None:
    from knowledge_mining.mining.kb.services.kb_service import (
        Forbidden,
        KbService,
    )

    class _Db:
        async def is_visible(self, **_kwargs):
            return True

        async def can_restore(self, **_kwargs):
            return False

        async def soft_delete(self, _kb_id):
            raise AssertionError("editor must not reach delete")

    with pytest.raises(Forbidden):
        await KbService(_Db()).soft_delete(kb_id="kb1", actor_id="editor")


@pytest.mark.asyncio
async def test_restore_is_idempotent_when_another_request_wins_race() -> None:
    from knowledge_mining.mining.kb.services.kb_service import KbService

    class _Db:
        reads = 0

        async def get_kb(self, _kb_id, *, include_deleted=False):
            self.reads += 1
            return {
                "id": "kb1",
                "status": "deleted" if self.reads == 1 else "active",
                "owner_id": "u1",
            }

        async def can_restore(self, **_kwargs):
            return True

        async def restore_kb(self, _kb_id):
            return None  # concurrent request already restored it

    restored = await KbService(_Db()).restore_kb(
        kb_id="kb1", actor_id="u1",
    )
    assert restored["status"] == "active"
