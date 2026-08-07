from __future__ import annotations

from contextlib import asynccontextmanager
import threading
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.api.routes import runs
from knowledge_mining.mining.api.routes import uploads
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


@pytest.mark.asyncio
async def test_create_run_inserts_real_queued_row_before_thread_start(monkeypatch):
    pool = _Pool()
    started = []

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target
            assert daemon is True

        def start(self):
            assert pool.conn.inserted is not None
            started.append(True)

    monkeypatch.setattr(runs.threading, "Thread", FakeThread)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                domain_pools=_DomainPools(pool),
                db_config=SimpleNamespace(),
            )
        )
    )
    body = runs.CreateRunRequest(input_path="C:/incoming", domain="odn")

    if runs._domain_run_lock("odn").locked():
        runs._domain_run_lock("odn").release()
    try:
        response = await runs.create_run(body, request)
    finally:
        if runs._domain_run_lock("odn").locked():
            runs._domain_run_lock("odn").release()

    assert started == [True]
    assert response["run_id"] != "pending"
    assert response == {
        "run_id": pool.conn.inserted["id"],
        "status": "queued",
        "current_stage": "queued",
        "started_at": pool.conn.inserted["started_at"],
        "execution_engine": "legacy",
        "workflow_id": None,
        "workflow_version": None,
        "workflow_graph_hash": None,
    }
    assert pool.conn.inserted["id"] != "previous-run"
    assert pool.conn.inserted["status"] == "queued"
    assert pool.conn.inserted["current_stage"] == "queued"


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


def test_slow_ingest_exposes_same_run_as_running_ingest_without_documents(monkeypatch):
    runtime_db = _RuntimeDB()
    runtime_db.insert_run(SimpleNamespace(
        id="submitted", input_path="C:/incoming", domain="odn", channel="prod",
        status="queued", current_stage="queued", total_documents=0,
    ))
    entered = threading.Event()
    release = threading.Event()

    def slow_ingest(path, params):
        entered.set()
        assert release.wait(5)
        return [], {"accepted": 0}

    _patch_worker(monkeypatch, runtime_db, slow_ingest)
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(run_job.run("C:/incoming", domain="odn", run_id="submitted"))
    )
    worker.start()
    assert entered.wait(5)
    assert runtime_db.row["id"] == "submitted"
    assert (runtime_db.row["status"], runtime_db.row["current_stage"]) == ("running", "ingest")
    assert runtime_db.documents == []
    release.set()
    worker.join(5)
    assert not worker.is_alive()


def test_ingest_failure_keeps_id_and_phase_and_records_failed_event(monkeypatch):
    runtime_db = _RuntimeDB()
    runtime_db.insert_run(SimpleNamespace(
        id="submitted", input_path="C:/incoming", domain="odn", channel="prod",
        status="queued", current_stage="queued", total_documents=0,
    ))

    def fail_ingest(path, params):
        raise RuntimeError("broken archive")

    _patch_worker(monkeypatch, runtime_db, fail_ingest)
    with pytest.raises(RuntimeError, match="broken archive"):
        run_job.run("C:/incoming", domain="odn", run_id="submitted")

    assert runtime_db.row["id"] == "submitted"
    assert (runtime_db.row["status"], runtime_db.row["current_stage"]) == ("failed", "ingest")
    assert runtime_db.row["error_summary"] == "broken archive"
    assert [(e["stage"], e["status"]) for e in runtime_db.events] == [
        ("ingest", "started"),
        ("ingest", "failed"),
    ]


def test_successful_ingest_updates_total_summary_and_single_event_pair(monkeypatch):
    runtime_db = _RuntimeDB()
    runtime_db.insert_run(SimpleNamespace(
        id="submitted", input_path="C:/incoming", domain="odn", channel="prod",
        status="queued", current_stage="queued", total_documents=0,
    ))
    docs = [SimpleNamespace(), SimpleNamespace()]
    _patch_worker(monkeypatch, runtime_db, lambda path, params: (docs, {"accepted": 2}))

    result = run_job.run("C:/incoming", domain="odn", run_id="submitted")

    assert result["run_id"] == "submitted"
    assert runtime_db.row["total_documents"] == 2
    assert runtime_db.row["metadata_json"]["ingest_summary"] == {"accepted": 2}
    assert runtime_db.row["current_stage"] == "mining"
    assert [(e["stage"], e["status"]) for e in runtime_db.events] == [
        ("ingest", "started"),
        ("ingest", "completed"),
    ]


def test_cli_run_creates_queued_before_entering_ingest(monkeypatch):
    runtime_db = _RuntimeDB()
    seen = []

    def ingest(path, params):
        seen.extend(runtime_db.transitions)
        return [], {}

    _patch_worker(monkeypatch, runtime_db, ingest)
    run_job.run("C:/incoming", domain="odn")

    assert seen[:2] == [("queued", "queued"), ("running", "ingest")]


def test_cancelled_during_ingest_is_not_failed_or_advanced(monkeypatch):
    runtime_db = _RuntimeDB()
    runtime_db.insert_run(SimpleNamespace(
        id="submitted", input_path="C:/incoming", domain="odn", channel="prod",
        status="queued", current_stage="queued", total_documents=0,
    ))

    def cancelled_ingest(path, params):
        runtime_db.row["status"] = "cancelled"
        raise RuntimeError("reader stopped")

    _patch_worker(monkeypatch, runtime_db, cancelled_ingest)
    result = run_job.run("C:/incoming", domain="odn", run_id="submitted")

    assert result == {"run_id": "submitted", "status": "cancelled"}
    assert runtime_db.row["status"] == "cancelled"
    assert runtime_db.row["current_stage"] == "ingest"
    assert runtime_db.documents == []


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
async def test_queued_insert_failure_releases_lock_and_never_starts_thread(monkeypatch):
    class Connection:
        async def execute(self, sql, params):
            raise RuntimeError("insert unavailable")

    class Pool:
        @asynccontextmanager
        async def connection(self):
            yield Connection()

    class Thread:
        def __init__(self, **kwargs):
            raise AssertionError("thread must not be constructed")

    monkeypatch.setattr(runs.threading, "Thread", Thread)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        domain_pools=_DomainPools(Pool()), db_config=SimpleNamespace(),
    )))
    if runs._domain_run_lock("odn").locked():
        runs._domain_run_lock("odn").release()

    with pytest.raises(RuntimeError, match="insert unavailable"):
        await runs.create_run(
            runs.CreateRunRequest(input_path="C:/incoming", domain="odn"), request
        )

    assert runs._domain_run_lock("odn").acquire(blocking=False) is True
    runs._domain_run_lock("odn").release()


@pytest.mark.asyncio
async def test_thread_start_failure_marks_same_queued_id_failed_and_releases_lock(monkeypatch):
    statements = []

    class Connection:
        async def execute(self, sql, params):
            statements.append((sql, list(params)))
            return _Cursor()

    class Pool:
        @asynccontextmanager
        async def connection(self):
            yield Connection()

    class Thread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(runs.threading, "Thread", Thread)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        domain_pools=_DomainPools(Pool()), db_config=SimpleNamespace(),
    )))
    if runs._domain_run_lock("odn").locked():
        runs._domain_run_lock("odn").release()

    with pytest.raises(Exception, match="Unable to start mining run"):
        await runs.create_run(
            runs.CreateRunRequest(input_path="C:/incoming", domain="odn"), request
        )

    inserted_id = statements[0][1][0]
    failed_update = next(item for item in statements if "SET status = 'failed'" in item[0])
    assert failed_update[1][2] == inserted_id
    assert "domain = %s" in failed_update[0]
    assert "status = 'queued'" in failed_update[0]
    assert runs._domain_run_lock("odn").acquire(blocking=False) is True
    runs._domain_run_lock("odn").release()


@pytest.mark.asyncio
async def test_queued_run_can_be_cancelled_before_worker_enters_ingest(monkeypatch):
    row = {
        "id": None,
        "domain": "odn",
        "status": "queued",
        "current_stage": "queued",
    }
    worker_called = []

    class Cursor:
        def __init__(self, value=None):
            self.value = value

        async def fetchone(self):
            return self.value

    class Connection:
        async def execute(self, sql, params):
            if "INSERT INTO mining_runs" in sql:
                row["id"] = params[0]
                return Cursor()
            if sql.startswith("SELECT"):
                return Cursor(dict(row))
            if "SET status = 'cancelled'" in sql:
                row["status"] = "cancelled"
                return Cursor({"status": "cancelled"})
            return Cursor()

    class Pool:
        @asynccontextmanager
        async def connection(self):
            yield Connection()

    class Thread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            worker_called.append(False)

    pool = Pool()
    monkeypatch.setattr(runs.threading, "Thread", Thread)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        domain_pools=_DomainPools(pool), db_config=SimpleNamespace(),
    )))
    if runs._domain_run_lock("odn").locked():
        runs._domain_run_lock("odn").release()
    try:
        created = await runs.create_run(
            runs.CreateRunRequest(input_path="C:/incoming", domain="odn"), request
        )
        cancelled = await runs.cancel_run(created["run_id"], request, "odn")
    finally:
        if runs._domain_run_lock("odn").locked():
            runs._domain_run_lock("odn").release()

    assert worker_called == [False]
    assert cancelled["status"] == "cancelled"
    assert row["status"] == "cancelled"
    assert row["current_stage"] == "queued"


@pytest.mark.asyncio
async def test_run_mutex_is_per_domain_not_global(monkeypatch):
    """一个域在挖掘不应阻塞其他域；同域内仍然互斥。"""

    class _AnyDomainPools:
        def __init__(self, pool):
            self.pool = pool

        async def async_pool(self, domain):
            return self.pool

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            pass  # 永不结束 —— 模拟挖掘中，锁保持占用

    monkeypatch.setattr(runs.threading, "Thread", FakeThread)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        domain_pools=_AnyDomainPools(_Pool()), db_config=SimpleNamespace(),
    )))

    busy = runs._domain_run_lock("odn")
    other = runs._domain_run_lock("generic")
    for lock in (busy, other):
        if lock.locked():
            lock.release()

    try:
        # odn 起一次挖掘 —— 占住 odn 的锁
        await runs.create_run(
            runs.CreateRunRequest(input_path="C:/incoming", domain="odn"), request
        )
        assert busy.locked() is True

        # 同域再提交 → 409
        with pytest.raises(Exception) as excinfo:
            await runs.create_run(
                runs.CreateRunRequest(input_path="C:/incoming", domain="odn"), request
            )
        assert excinfo.value.status_code == 409
        assert "odn" in excinfo.value.detail

        # 另一个域不受影响
        response = await runs.create_run(
            runs.CreateRunRequest(input_path="C:/incoming", domain="generic"), request
        )
        assert response["status"] == "queued"
        assert other.locked() is True
    finally:
        for lock in (busy, other):
            if lock.locked():
                lock.release()


def test_submission_contract_has_no_latest_run_or_pending_response():
    from pathlib import Path

    source = Path(runs.__file__).read_text(encoding="utf-8")
    create_source = source[source.index("async def create_run"):source.index("@router.get(\"\")")]

    assert "ORDER BY started_at DESC LIMIT 1" not in create_source
    assert '"pending"' not in create_source
    assert "domain_pools.async_pool" in create_source
    assert "run_id=run_id" in create_source


@pytest.mark.asyncio
async def test_legacy_mode_rejects_explicit_workflow_before_inserting(monkeypatch):
    class Config:
        mining_run_submission_engine = "legacy"
        llm_service_url = "http://localhost:8900"

    class DomainPools:
        async def async_pool(self, domain):
            raise AssertionError("rejected request must not open a Domain pool")

    monkeypatch.setattr(runs, "MiningConfig", Config)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        domain_pools=DomainPools(), db_config=SimpleNamespace(),
    )))

    with pytest.raises(Exception) as excinfo:
        await runs.create_run(
            runs.CreateRunRequest(
                input_path="C:/incoming", domain="odn", workflow_id="wf"
            ),
            request,
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["code"] == "workflow_engine_unavailable"


@pytest.mark.asyncio
async def test_workflow_mode_binds_full_and_inserts_manifest_before_thread(
    monkeypatch, tmp_path
):
    pool = _Pool()
    inserted = []
    binder_calls = []
    started = []

    class Config:
        mining_run_submission_engine = "workflow"
        llm_service_url = "http://localhost:8900"

    class Binder:
        async def resolve(self, **kwargs):
            binder_calls.append(kwargs)
            return WorkflowRunBinding(
                workflow_id="system-full-baseline",
                workflow_version=4,
                workflow_version_id="version-full-4",
                graph_hash="graph-hash",
                manifest={"runtimeBinding": {"domain": "odn"}},
            )

    class Repository:
        def __init__(self, selected_pool):
            assert selected_pool is pool

        async def insert_queued_run(self, **kwargs):
            inserted.append(kwargs)

    class Thread:
        def __init__(self, *, target, daemon):
            self.target = target
            assert daemon is True

        def start(self):
            assert inserted
            started.append(True)

    batch_path = tmp_path / "odn" / "abcdef123456"
    batch_path.mkdir(parents=True)
    monkeypatch.setattr(runs, "MiningConfig", Config)
    monkeypatch.setattr(runs, "AsyncDomainRunRepository", Repository)
    monkeypatch.setattr(
        runs,
        "resolve_upload_batch_path",
        lambda domain, batch_id: (
            batch_path
            if (domain, batch_id) == ("odn", "abcdef123456")
            else (_ for _ in ()).throw(AssertionError("wrong batch lookup"))
        ),
    )
    monkeypatch.setattr(runs, "resolve_domain", lambda domain: {
        "id": domain, "default_channel": "prod"
    })
    monkeypatch.setattr(runs.threading, "Thread", Thread)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        domain_pools=_DomainPools(pool),
        db_config=SimpleNamespace(),
        workflow_run_binder=Binder(),
    )))
    lock = runs._domain_run_lock("odn")
    if lock.locked():
        lock.release()
    try:
        response = await runs.create_run(
            runs.CreateRunRequest(
                domain="odn",
                upload_batch_id="abcdef123456",
                max_workers=8,
                phase1_only=True,
                publish_on_partial_failure=True,
            ),
            request,
        )
    finally:
        if lock.locked():
            lock.release()

    assert started == [True]
    assert binder_calls == [{
        "workflow_id": None,
        "workflow_version": None,
        "domain": "odn",
        "channel": "prod",
        "upload_batch_id": "abcdef123456",
        "run_overrides": {
            "maxWorkers": 8,
            "executionMode": "assets_only",
            "publishOnPartialFailure": True,
        },
    }]
    assert inserted[0]["input_path"] == str(batch_path.resolve())
    assert inserted[0]["execution_engine"] == "workflow"
    assert inserted[0]["binding"].workflow_version == 4
    assert response["execution_engine"] == "workflow"
    assert response["workflow_id"] == "system-full-baseline"
    assert response["workflow_version"] == 4
    assert response["workflow_graph_hash"] == "graph-hash"


@pytest.mark.asyncio
async def test_upload_config_exposes_submission_engine(monkeypatch):
    class Config:
        mining_run_submission_engine = "workflow"

    monkeypatch.setattr(uploads, "MiningConfig", Config)

    result = await uploads.get_upload_config()

    assert result["mining_run_submission_engine"] == "workflow"
    assert ".xls" in result["accepted_extensions"]
    assert ".xlsx" in result["accepted_extensions"]


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
