from __future__ import annotations

from contextlib import asynccontextmanager
import threading
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.api.routes import runs
from knowledge_mining.mining.jobs import run as run_job
from knowledge_mining.mining.runtime import RuntimeTracker
from knowledge_mining.mining.workflow.run_binding import WorkflowRunBinding


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _Connection:
    def __init__(self):
        self.inserted: dict[str, object] | None = None

    async def execute(self, sql, params):
        if "INSERT INTO mining_runs" in sql:
            self.inserted = {
                "id": params[0],
                "input_path": params[1],
                "domain": params[2],
                "status": params[3],
                "current_stage": params[4],
                "started_at": params[5],
            }
            return _Cursor()
        if "ORDER BY started_at DESC LIMIT 1" in sql:
            return _Cursor(
                {
                    "id": "previous-run",
                    "status": "completed",
                    "started_at": "2020-01-01T00:00:00+00:00",
                }
            )
        return _Cursor()


class _Pool:
    def __init__(self):
        self.conn = _Connection()

    @asynccontextmanager
    async def connection(self):
        yield self.conn


class _DomainPools:
    def __init__(self, pool):
        self.pool = pool

    async def async_pool(self, domain):
        assert domain == "odn"
        return self.pool


class _LegacyEngineConfig:
    """conftest 全局把提交引擎钉为 workflow；验证 legacy 提交路径的用例显式钉回 legacy."""

    mining_run_submission_engine = "legacy"
    llm_service_url = "http://localhost:8900"


@pytest.mark.asyncio
class _RuntimeDB:
    def __init__(self):
        self.row = None
        self.events = []
        self.transitions = []
        self.documents = []

    def insert_run(self, data):
        self.row = dict(data.__dict__)
        self.transitions.append((data.status, data.current_stage))

    def get_run(self, run_id):
        return self.row if self.row and self.row["id"] == run_id else None

    def set_run_phase(self, run_id, domain, current_stage, *, status="running"):
        if not self.row or self.row["status"] not in ("queued", "running"):
            return False
        self.row.update(status=status, current_stage=current_stage)
        self.transitions.append((status, current_stage))
        return True

    def insert_stage_event(self, event):
        self.events.append(dict(event.__dict__))

    def _fetchone(self, sql, params=()):
        if "FROM mining_run_stage_events" in sql:
            event_id = params[0]
            event = next(e for e in self.events if e["id"] == event_id)
            return {
                "created_at": event["created_at"],
                "run_document_id": event["run_document_id"],
            }
        if "SELECT status FROM mining_runs" in sql:
            return {"status": self.row["status"]}
        return None

    def finish_ingest(self, run_id, domain, total_documents, ingest_summary):
        if self.row["status"] not in ("queued", "running"):
            return False
        self.row.update(
            status="running",
            current_stage="mining",
            total_documents=total_documents,
            metadata_json={"ingest_summary": ingest_summary},
        )
        self.transitions.append(("running", "mining"))
        return True

    def fail_run(self, run_id, domain, error_summary, current_stage):
        if self.row["status"] not in ("queued", "running"):
            return False
        self.row.update(
            status="failed",
            current_stage=current_stage,
            error_summary=error_summary,
        )
        return True

    def commit(self):
        pass

    def close(self):
        pass


class _AssetDB:
    pool = object()

    def close(self):
        pass


def test_create_dbs_does_not_initialize_domain_schema_for_worker_jobs(monkeypatch):
    """Schema migrations are a startup concern, never a per-job side effect."""
    schema_initializations = []

    class FakeConnectionPool:
        check_connection = object()

        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    import psycopg_pool
    from knowledge_mining.mining.infra import domain_db

    monkeypatch.setattr(
        run_job,
        "ensure_domain_database_schema",
        lambda resolved: schema_initializations.append(resolved),
        raising=False,
    )
    monkeypatch.setattr(
        domain_db,
        "ensure_domain_database_schema",
        lambda resolved: schema_initializations.append(resolved),
    )
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", FakeConnectionPool)

    resolved = run_job.ResolvedDomainDatabase(
        conninfo="host=localhost dbname=mining_test",
        pool_min=1,
        pool_max=2,
        source="inline",
    )

    asset_db, runtime_db = run_job._create_dbs(resolved)

    assert schema_initializations == []
    assert asset_db.pool is runtime_db.pool


def _patch_worker(monkeypatch, runtime_db, ingest):
    monkeypatch.setattr(run_job, "_create_dbs", lambda resolved: (_AssetDB(), runtime_db))
    monkeypatch.setattr(run_job, "resolve_domain", lambda domain: {"id": domain, "default_channel": "prod"})
    monkeypatch.setattr(run_job, "resolve_domain_database", lambda entry, config: object())
    monkeypatch.setattr(run_job, "load_domain_pack", lambda domain: SimpleNamespace(domain_id=domain))
    monkeypatch.setattr(run_job, "_init_llm", lambda *args, **kwargs: {})
    monkeypatch.setattr(run_job, "_init_embedding", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_job, "ingest_directory", ingest)
    monkeypatch.setattr(
        run_job,
        "_run_pipeline",
        lambda *args, **kwargs: {"run_id": args[5], "status": "running"},
    )


def test_resume_running_moves_run_phase_back_to_mining():
    calls = []

    class DB:
        def update_run_status(self, *args, **kwargs):
            calls.append((args, kwargs))
            return True

    updated = RuntimeTracker(DB()).resume_running(
        "reviewed-run", subloop_stage="done", domain="odn"
    )

    assert updated is True
    assert calls == [
        (
            ("reviewed-run", "running"),
            {
                "subloop_stage": "done",
                "current_stage": "mining",
                "domain": "odn",
                "expected_statuses": ("awaiting_review", "running"),
            },
        )
    ]


@pytest.mark.parametrize(
    ("engine", "status", "stage", "finished_at", "expected"),
    [
        ("workflow", "awaiting_review", "entity_review", None, True),
        ("workflow", "failed", "mining", "2026-07-25T00:00:00Z", True),
        ("workflow", "interrupted", "mining", "2026-07-25T00:00:00Z", True),
        ("workflow", "running", "graph_write", None, True),
        ("legacy", "failed", "mining", "2026-07-25T00:00:00Z", False),
        ("legacy", "running", "graph_write", None, False),
        ("legacy", "running", "done", None, True),
        ("workflow", "cancelled", "mining", None, False),
    ],
)
def test_public_resume_policy_exposes_workflow_crash_recovery(
    engine, status, stage, finished_at, expected
):
    assert runs._is_run_resumable(
        execution_engine=engine,
        status=status,
        subloop_stage=stage,
        finished_at=finished_at,
    ) is expected


def test_workflow_recovery_claim_clears_terminal_fields_and_accepts_failed():
    calls = []

    class DB:
        def update_run_status(self, *args, **kwargs):
            calls.append((args, kwargs))
            return True

    updated = RuntimeTracker(DB()).resume_running(
        "failed-run",
        subloop_stage="graph_write",
        domain="odn",
        recover_workflow=True,
    )

    assert updated is True
    assert calls == [
        (
            ("failed-run", "running"),
            {
                "subloop_stage": "graph_write",
                "current_stage": "mining",
                "domain": "odn",
                "expected_statuses": (
                    "awaiting_review",
                    "running",
                    "failed",
                    "interrupted",
                ),
                "clear_finished_at": True,
                "clear_error_summary": True,
            },
        )
    ]


def test_manual_workflow_publish_claims_completed_assets_run():
    calls = []

    class DB:
        def update_run_status(self, *args, **kwargs):
            calls.append((args, kwargs))
            return True

    updated = RuntimeTracker(DB()).begin_manual_publish(
        "assets-run", domain="odn"
    )

    assert updated is True
    assert calls == [
        (
            ("assets-run", "running"),
            {
                "current_stage": "mining",
                "domain": "odn",
                "expected_statuses": ("completed",),
                "clear_finished_at": True,
                "clear_error_summary": True,
            },
        )
    ]


@pytest.mark.parametrize("pending_gate", ["entity_review", "ontology_review", None])
def test_resume_cas_returns_concurrent_cancel_status(monkeypatch, pending_gate):
    class RuntimeDB:
        def __init__(self):
            self.row = {
                "id": "reviewed-run",
                "status": "awaiting_review",
                "subloop_stage": "ontology_review",
                "domain": "odn",
                "source_batch_id": "batch-1",
                "total_documents": 1,
            }
            self.calls = []

        def get_run(self, run_id):
            assert run_id == "reviewed-run"
            return dict(self.row)

        def update_run_status(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            self.row["status"] = "cancelled"
            return False

        def commit(self):
            raise AssertionError("a failed resume CAS must not continue")

        def close(self):
            pass

    runtime_db = RuntimeDB()
    asset_db = _AssetDB()
    monkeypatch.setattr(run_job, "resolve_domain", lambda domain: {"id": domain})
    monkeypatch.setattr(run_job, "resolve_domain_database", lambda entry, config: object())
    monkeypatch.setattr(run_job, "_create_dbs", lambda resolved: (asset_db, runtime_db))
    monkeypatch.setattr(run_job, "load_domain_pack", lambda domain: SimpleNamespace(domain_id=domain))
    monkeypatch.setattr(
        run_job,
        "_has_pending_mentions",
        lambda asset, run_id: pending_gate == "entity_review",
    )
    monkeypatch.setattr(
        run_job,
        "_has_proposed_candidates",
        lambda asset, domain: pending_gate == "ontology_review",
    )
    monkeypatch.setattr(
        run_job,
        "_finalize_graph",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("a failed resume CAS must not finalize")
        ),
    )

    result = run_job.resume("reviewed-run", domain="odn")

    assert result == {"run_id": "reviewed-run", "status": "cancelled"}
    assert runtime_db.calls[0][1]["expected_statuses"] == (
        "awaiting_review",
        "running",
    )


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
def test_preprocess_log_has_identifiers_but_not_cell_content(caplog):
    from knowledge_mining.mining.jobs import run as run_job

    run_job._log_preprocess_diagnostics(
        run_id="run-1",
        run_document_id="rd-1",
        document_key="doc:/bad.xlsx",
        metadata={
            "preprocess_status": "failed",
            "preprocess_error_code": "excel_corrupt_file",
            "preprocess_warnings": [],
            "sentinel_cell_content": "SECRET-CELL-VALUE",
        },
    )

    text = caplog.text
    assert "run-1" in text and "rd-1" in text
    assert "excel_corrupt_file" in text
    assert "SECRET-CELL-VALUE" not in text


def test_copy_preprocess_metadata_keeps_only_public_diagnostics():
    from knowledge_mining.mining.jobs import run as run_job

    target = {"file_size": 10}
    run_job._copy_preprocess_metadata(
        target,
        {
            "preprocess_status": "partial",
            "preprocess_error_code": None,
            "preprocess_error": None,
            "preprocess_warnings": [{"code": "excel_formula_cache_missing"}],
            "excel_summary": {"sheet_count": 2},
            "sentinel_cell_content": "SECRET-CELL-VALUE",
        },
    )

    assert target["preprocess_status"] == "partial"
    assert target["excel_summary"] == {"sheet_count": 2}
    assert "sentinel_cell_content" not in target


@pytest.mark.asyncio
async def test_cancel_cas_reports_terminal_race_instead_of_false_cancelled():
    calls = 0

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        async def execute(self, sql, params):
            nonlocal calls
            calls += 1
            if "UPDATE mining_runs" in sql:
                assert "RETURNING status" in sql
                return Cursor(None)
            if calls <= 2:
                return Cursor({"id": "r1", "domain": "odn", "status": "running"})
            return Cursor({"id": "r1", "status": "completed"})

    class Pool:
        @asynccontextmanager
        async def connection(self):
            yield Connection()

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        domain_pools=_DomainPools(Pool()),
    )))

    with pytest.raises(Exception, match="completed, cannot cancel"):
        await runs.cancel_run("r1", request, "odn")


@pytest.mark.asyncio
async def test_cancel_cas_rejects_run_claimed_for_publishing():
    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        async def execute(self, sql, params):
            if "UPDATE mining_runs" in sql:
                assert "current_stage <> 'publishing'" in sql
                return Cursor(None)
            if "SELECT id, domain, status" in sql:
                return Cursor({"id": "r1", "domain": "odn", "status": "running"})
            assert "current_stage" in sql
            return Cursor({"id": "r1", "status": "running", "current_stage": "publishing"})

    class Pool:
        @asynccontextmanager
        async def connection(self):
            yield Connection()

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        domain_pools=_DomainPools(Pool()),
    )))

    with pytest.raises(Exception, match="publishing, cannot cancel"):
        await runs.cancel_run("r1", request, "odn")


@pytest.mark.asyncio
async def test_cancel_cas_rejects_run_claimed_for_publishing():
    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        async def execute(self, sql, params):
            if "UPDATE mining_runs" in sql:
                assert "current_stage <> 'publishing'" in sql
                return Cursor(None)
            if "SELECT id, domain, status" in sql:
                return Cursor({"id": "r1", "domain": "odn", "status": "running"})
            assert "current_stage" in sql
            return Cursor({"id": "r1", "status": "running", "current_stage": "publishing"})

    class Pool:
        @asynccontextmanager
        async def connection(self):
            yield Connection()

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        domain_pools=_DomainPools(Pool()),
    )))

    with pytest.raises(Exception, match="publishing, cannot cancel"):
        await runs.cancel_run("r1", request, "odn")


@pytest.mark.asyncio
async def test_batch_mining_endpoints_are_retired():
    """批次/域级挖掘通道退役（决策 2026-08-27）：POST /api/runs 不得存在。

    挖掘必须基于知识库（POST /api/kb/{kb_id}/mine）。批次通道写 kb_id=NULL
    的域级文档且 v2 解析链不认领（document_parse_unavailable 必败）。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(runs.router)
    client = TestClient(app)
    try:
        assert client.post("/api/runs", json={"domain": "generic", "input_path": "/x"}).status_code == 405
        assert client.post("/api/runs/preflight", json={"domain": "generic"}).status_code in (404, 405)
        assert client.post("/api/uploads", files={"files": ("a.txt", b"x")}).status_code == 404
    finally:
        client.close()
