from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest


def _candidate(run_id: str, *, status: str = "queued", domain: str = "odn") -> dict:
    return {
        "id": run_id,
        "domain": domain,
        "status": status,
        "input_path": f"/input/{run_id}",
        "kb_id": f"kb-{run_id}",
    }


def test_dispatcher_drains_one_domain_in_fifo_order() -> None:
    from knowledge_mining.mining.api.domain_run_queue import DomainRunQueueDispatcher

    pending = [_candidate("a"), _candidate("b")]
    executed: list[str] = []

    dispatcher = DomainRunQueueDispatcher(
        next_candidate=lambda _domain: pending.pop(0) if pending else None,
        execute_run=lambda candidate: executed.append(candidate["id"]) or {"status": "completed"},
        resume_run=lambda candidate: executed.append(f"resume:{candidate['id']}") or {"status": "completed"},
        record_failure=lambda _candidate, _error: None,
    )

    assert dispatcher.drain("odn") == 2
    assert executed == ["a", "b"]


def test_dispatcher_resumes_interrupted_candidate_and_continues_after_failure() -> None:
    from knowledge_mining.mining.api.domain_run_queue import DomainRunQueueDispatcher

    pending = [_candidate("old", status="interrupted"), _candidate("next")]
    events: list[str] = []

    def resume(candidate: dict) -> dict:
        events.append(f"resume:{candidate['id']}")
        raise RuntimeError("resume failed")

    dispatcher = DomainRunQueueDispatcher(
        next_candidate=lambda _domain: pending.pop(0) if pending else None,
        execute_run=lambda candidate: events.append(f"run:{candidate['id']}") or {"status": "completed"},
        resume_run=resume,
        record_failure=lambda candidate, _error: events.append(f"failed:{candidate['id']}"),
    )

    assert dispatcher.drain("odn") == 2
    assert events == ["resume:old", "failed:old", "run:next"]


def test_dispatcher_exits_when_another_instance_owns_the_domain() -> None:
    from knowledge_mining.mining.api.domain_run_queue import DomainRunQueueDispatcher

    pending = [_candidate("a"), _candidate("b")]
    executed: list[str] = []
    dispatcher = DomainRunQueueDispatcher(
        next_candidate=lambda _domain: pending[0] if pending else None,
        execute_run=lambda candidate: executed.append(candidate["id"]) or {"status": "claimed_elsewhere"},
        resume_run=lambda _candidate: {"status": "claimed_elsewhere"},
        record_failure=lambda _candidate, _error: None,
    )

    assert dispatcher.drain("odn") == 0
    assert executed == ["a"]
    assert [item["id"] for item in pending] == ["a", "b"]


def test_e2e_second_kb_stays_queued_then_runs_after_first_finishes() -> None:
    """User journey: A running -> B queued -> A terminal -> B runs automatically."""
    from knowledge_mining.mining.api.domain_run_queue import DomainRunQueueDispatcher

    rows = [_candidate("a")]
    guard = threading.Lock()
    first_started = threading.Event()
    release_first = threading.Event()
    all_done = threading.Event()
    active = 0
    max_active = 0

    def next_candidate(_domain: str) -> dict | None:
        with guard:
            return next((dict(row) for row in rows if row["status"] == "queued"), None)

    def execute(candidate: dict) -> dict:
        nonlocal active, max_active
        with guard:
            row = next(row for row in rows if row["id"] == candidate["id"])
            row["status"] = "running"
            active += 1
            max_active = max(max_active, active)
        if candidate["id"] == "a":
            first_started.set()
            assert release_first.wait(3)
        with guard:
            row["status"] = "completed"
            active -= 1
            if all(item["status"] == "completed" for item in rows):
                all_done.set()
        return {"status": "completed"}

    dispatcher = DomainRunQueueDispatcher(
        next_candidate=next_candidate,
        execute_run=execute,
        resume_run=execute,
        record_failure=lambda _candidate, _error: None,
    )
    dispatcher.kick("odn")
    assert first_started.wait(3)
    with guard:
        rows.append(_candidate("b"))
    assert dispatcher.kick("odn") is False
    with guard:
        assert [(row["id"], row["status"]) for row in rows] == [
            ("a", "running"), ("b", "queued"),
        ]

    release_first.set()
    assert all_done.wait(3)
    assert max_active == 1
    dispatcher.close()


def test_production_dispatcher_uses_persisted_candidate_and_existing_run_job(
    monkeypatch,
) -> None:
    from knowledge_mining.mining.api.domain_run_queue import build_domain_run_dispatcher
    from knowledge_mining.mining.jobs import run as run_job

    rows = [_candidate("persisted")]
    calls = []

    class Cursor:
        def fetchone(self):
            return rows.pop(0) if rows else None

    class Connection:
        def execute(self, sql, params):
            assert "ORDER BY" in sql and "started_at" in sql
            assert params == ("odn",)
            return Cursor()

    class SyncConnectionContext:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return None

    class SyncPool:
        def connection(self):
            return SyncConnectionContext()

    monkeypatch.setattr(
        run_job, "run",
        lambda input_path, **kwargs: calls.append((input_path, kwargs)) or {"status": "completed"},
    )
    dispatcher = build_domain_run_dispatcher(
        SimpleNamespace(sync_pool=lambda _domain: SyncPool()), object(),
    )

    assert dispatcher.drain("odn") == 1
    assert calls[0][0] == "/input/persisted"
    assert calls[0][1]["run_id"] == "persisted"


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class _AsyncConnection:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    async def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))
        return _Cursor(self.row)


class _AsyncPool:
    def __init__(self, row=None):
        self.conn = _AsyncConnection(row)

    @asynccontextmanager
    async def connection(self):
        yield self.conn


@pytest.mark.asyncio
async def test_repository_finds_one_open_run_for_the_same_kb() -> None:
    from knowledge_mining.mining.workflow.repositories.domain_run_repository import (
        AsyncDomainRunRepository,
    )

    pool = _AsyncPool({"id": "r1", "status": "running"})
    row = await AsyncDomainRunRepository(pool).find_open_run_for_kb("kb-1")

    assert row == {"id": "r1", "status": "running"}
    sql, params = pool.conn.calls[0]
    assert "kb_id = %s" in sql
    assert "awaiting_review" in sql and "interrupted" in sql
    assert params == ("kb-1",)


@pytest.mark.asyncio
async def test_queued_run_is_inserted_atomically_with_kb_and_submitter_metadata() -> None:
    from knowledge_mining.mining.workflow.repositories.domain_run_repository import (
        AsyncDomainRunRepository,
    )
    from knowledge_mining.mining.workflow.run_binding import WorkflowRunBinding

    binding = WorkflowRunBinding(
        workflow_id="wf", workflow_version=1, workflow_version_id="wfv-1",
        graph_hash="hash", manifest={"workflowId": "wf"},
    )
    pool = _AsyncPool()
    await AsyncDomainRunRepository(pool).insert_queued_run(
        run_id="r1", input_path="/input", domain="odn", channel="prod",
        execution_engine="workflow", binding=binding,
        started_at="2026-09-01T00:00:00Z", kb_id="kb-1",
        metadata_json={"submitted_by_user_id": "u1"},
    )

    sql, params = pool.conn.calls[0]
    assert "kb_id" in sql and "metadata_json" in sql
    assert "kb-1" in params
    assert any(
        getattr(value, "obj", None) == {"submitted_by_user_id": "u1"}
        for value in params
    )


def test_queue_migration_is_in_domain_schema_and_has_one_open_run_per_kb() -> None:
    from knowledge_mining.mining.infra.pg_schema import domain_schema_paths

    paths = domain_schema_paths()
    migration = next(path for path in paths if path.name == "009_mining_run_kb_queue.sql")
    ddl = migration.read_text(encoding="utf-8").lower()

    assert "unique index" in ddl
    assert "kb_id" in ddl
    for status in ("queued", "running", "awaiting_review", "interrupted"):
        assert status in ddl


class _KbDb:
    def __init__(self):
        self.kb = {
            "id": "kb-1", "domain": "odn", "mining_workflow_id": "wf",
        }

    async def get_kb(self, _kb_id):
        return self.kb

    async def is_visible(self, **_kwargs):
        return True

    async def can_write(self, **_kwargs):
        return True

    async def list_documents_in_kb(self, **_kwargs):
        return [{"id": "doc-1"}]


@pytest.mark.asyncio
async def test_api_rejects_duplicate_run_for_the_same_kb(monkeypatch) -> None:
    from knowledge_mining.mining.kb.routes import mining

    class Repo:
        def __init__(self, _pool):
            pass

        async def find_open_run_for_kb(self, _kb_id):
            return {"id": "existing", "status": "running"}

    monkeypatch.setattr(mining, "AsyncDomainRunRepository", Repo)
    monkeypatch.setattr(mining, "resolve_domain", lambda _domain: {"default_channel": "prod"})
    monkeypatch.setattr(
        mining, "UploadConfig",
        lambda: SimpleNamespace(upload_root_path=Path("/uploads")),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        db_config=object(),
        domain_pools=SimpleNamespace(async_pool=lambda _domain: _async_value(object())),
    )))

    with pytest.raises(Exception) as error:
        await mining.mine_kb(
            "kb-1", request, {"id": "u1", "username": "alice"},
            _KbDb(), None,
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "kb_mining_busy"
    assert error.value.detail["details"] == {
        "run_id": "existing", "status": "running",
    }


@pytest.mark.asyncio
async def test_api_accepts_other_kb_as_queued_and_kicks_dispatcher(monkeypatch) -> None:
    from knowledge_mining.mining.kb.routes import mining

    inserted = {}
    kicked = []

    class Repo:
        def __init__(self, _pool):
            pass

        async def find_open_run_for_kb(self, _kb_id):
            return None

        async def insert_queued_run(self, **kwargs):
            inserted.update(kwargs)

    async def resolve(**_kwargs):
        return SimpleNamespace(
            workflow_id="wf", workflow_version=1,
            workflow_version_id="wfv-1", graph_hash="hash",
            manifest={"workflowId": "wf"},
        )

    monkeypatch.setattr(mining, "AsyncDomainRunRepository", Repo)
    monkeypatch.setattr(mining, "resolve_domain", lambda _domain: {"default_channel": "prod"})
    monkeypatch.setattr(
        mining, "UploadConfig",
        lambda: SimpleNamespace(upload_root_path=Path("/uploads")),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        db_config=object(),
        workflow_run_binder=SimpleNamespace(resolve=resolve),
        domain_pools=SimpleNamespace(async_pool=lambda _domain: _async_value(_SigPool())),
        domain_run_dispatcher=SimpleNamespace(kick=lambda domain: kicked.append(domain)),
    )))

    response = await mining.mine_kb(
        "kb-1", request, {"id": "u1", "username": "alice"},
        _KbDb(), None,
    )

    assert response["status"] == "queued"
    assert inserted["kb_id"] == "kb-1"
    assert inserted["metadata_json"]["submitted_by_user_id"] == "u1"
    assert kicked == ["odn"]


async def _async_value(value):
    return value


class _SigCursor:
    async def fetchone(self):
        return None


class _SigConnection:
    async def execute(self, _sql, _params):
        return _SigCursor()


class _SigPool:
    @asynccontextmanager
    async def connection(self):
        yield _SigConnection()


@pytest.mark.asyncio
async def test_asgi_e2e_same_kb_is_friendly_conflict_other_kb_is_queued(
    monkeypatch,
) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from knowledge_mining.mining.kb.routes import mining

    inserted: list[dict] = []
    kicked: list[str] = []

    class Repo:
        def __init__(self, _pool):
            pass

        async def find_open_run_for_kb(self, kb_id):
            if kb_id == "kb-a":
                return {"id": "run-a", "status": "running"}
            return None

        async def insert_queued_run(self, **kwargs):
            inserted.append(kwargs)

    class KbDb(_KbDb):
        async def get_kb(self, kb_id):
            return {**self.kb, "id": kb_id}

    async def resolve(**_kwargs):
        return SimpleNamespace(
            workflow_id="wf", workflow_version=1,
            workflow_version_id="wfv-1", graph_hash="hash",
            manifest={"workflowId": "wf"},
        )

    monkeypatch.setattr(mining, "AsyncDomainRunRepository", Repo)
    monkeypatch.setattr(mining, "resolve_domain", lambda _domain: {"default_channel": "prod"})
    monkeypatch.setattr(
        mining, "UploadConfig",
        lambda: SimpleNamespace(upload_root_path=Path("/uploads")),
    )
    app = FastAPI()
    app.state.db_config = object()
    app.state.workflow_run_binder = SimpleNamespace(resolve=resolve)
    app.state.domain_pools = SimpleNamespace(
        async_pool=lambda _domain: _async_value(_SigPool()),
    )
    app.state.domain_run_dispatcher = SimpleNamespace(
        kick=lambda domain: kicked.append(domain),
    )
    app.dependency_overrides[mining.current_user] = lambda: {
        "id": "u1", "username": "alice",
    }
    app.dependency_overrides[mining.get_kb_db] = KbDb
    app.include_router(mining.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        busy = await client.post("/api/kb/kb-a/mine")
        queued = await client.post("/api/kb/kb-b/mine")

    assert busy.status_code == 409
    assert busy.json()["detail"]["code"] == "kb_mining_busy"
    assert busy.json()["detail"]["message"] == "该知识库正在挖掘，请完成后再试"
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"
    assert inserted[0]["kb_id"] == "kb-b"
    assert kicked == ["odn"]
