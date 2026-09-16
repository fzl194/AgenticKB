"""Tests for the admin retention cleanup (POST /api/v1/admin/cleanup).

Locks the safety contract: dry_run never deletes, terminal-status whitelist
(including sync-path 'failed') is hardcoded SQL, children deleted before
parents in FK-safe order, per-batch scoping, model_calls independent loop,
budget truncation semantics, and request-model bounds.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from llm_service.models import CleanupRequest
from llm_service.runtime import cleanup
from llm_service.runtime.cleanup import run_cleanup

_CUTOFF = datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)


class _FakeCursor:
    def __init__(self, rowcount: int = 0, rows: list | None = None):
        self.rowcount = rowcount
        self._rows = rows or []

    async def fetchall(self):
        return self._rows


class _FakeConn:
    """Async psycopg-like conn: records every execute, pops canned results."""

    def __init__(self, db: "_FakeCleanupDB"):
        self._db = db

    async def execute(self, sql, params=()):
        self._db.conn_log.append((sql, params))
        if sql.startswith("SELECT"):
            rows = (
                self._db.verify_batches.pop(0)
                if self._db.verify_batches
                else []
            )
            return _FakeCursor(rows=[{"id": i} for i in rows])
        rowcount = (
            self._db.delete_rowcounts.pop(0)
            if self._db.delete_rowcounts
            else 0
        )
        return _FakeCursor(rowcount)


class _FakeCleanupDB:
    """SQL-capturing fake for run_cleanup's query surface."""

    def __init__(self, *, task_batches=None, verify_batches=None,
                 delete_rowcounts=None, counts=None):
        # Each entry = one `SELECT id ... LIMIT` result; missing/empty ends loop A.
        self.task_batches = list(task_batches or [])
        # Each entry = the FOR UPDATE re-verify result inside one batch txn.
        self.verify_batches = list(verify_batches or [])
        # Shared rowcount queue consumed by every conn.execute (delete order).
        self.delete_rowcounts = list(delete_rowcounts or [])
        # Per-table COUNT(*) results used by dry-run estimates / remaining.
        self.counts = counts or {}
        self.sql_log: list[tuple[str, tuple]] = []  # fetchone/fetchall queries
        self.conn_log: list[tuple[str, tuple]] = []  # conn.execute (deletes)

    async def fetchone(self, sql, params=()):
        self.sql_log.append((sql, params))
        if "make_interval" in sql:
            return {"cutoff": _CUTOFF}
        if "COUNT(*)" in sql:
            return {"count": self.counts.get(self._count_key(sql), 0)}
        raise AssertionError(f"unexpected fetchone SQL: {sql}")

    async def fetchall(self, sql, params=()):
        self.sql_log.append((sql, params))
        if "SELECT id FROM agent_llm_tasks" in sql:
            ids = self.task_batches.pop(0) if self.task_batches else []
            return [{"id": i} for i in ids]
        if "pg_class" in sql:
            return [
                {"table_name": t, "approx_rows": 0, "total_bytes": 0}
                for t in ("agent_llm_tasks", "agent_llm_model_calls")
            ]
        raise AssertionError(f"unexpected fetchall SQL: {sql}")

    async def run(self, fn):
        return await fn(_FakeConn(self))

    @staticmethod
    def _count_key(sql: str) -> str:
        if "agent_llm_model_calls" in sql:
            return "model_calls"
        if "agent_llm_requests" in sql:
            return "requests"
        if "agent_llm_attempts" in sql:
            return "attempts"
        if "agent_llm_results" in sql:
            return "results"
        if "agent_llm_events" in sql:
            return "events"
        return "tasks"


def _delete_sqls(db: _FakeCleanupDB) -> list[str]:
    """Only DELETE statements issued on connections (skips verify SELECTs)."""
    return [sql for sql, _ in db.conn_log if sql.startswith("DELETE")]


@pytest.mark.asyncio
async def test_dry_run_never_deletes():
    db = _FakeCleanupDB(counts={"tasks": 10, "model_calls": 5})
    out = await run_cleanup(db, retention_days=30, dry_run=True)
    assert db.conn_log == []  # no conn.execute at all
    assert all("DELETE" not in sql for sql, _ in db.sql_log)
    assert out["dry_run"] is True
    assert out["estimates"]["tasks"] == 10
    assert out["estimates"]["model_calls"] == 5
    assert out["truncated"] is False
    assert out["notes"]


@pytest.mark.asyncio
async def test_delete_order_children_before_parents_fk_safe():
    db = _FakeCleanupDB(
        task_batches=[["t1", "t2", "t3"]],
        verify_batches=[["t1", "t2", "t3"]],
        # results, events, attempts, requests, tasks, then model_calls=0
        delete_rowcounts=[3, 3, 3, 3, 3, 0],
    )
    out = await run_cleanup(db, retention_days=30, dry_run=False)
    order = [sql.removeprefix("DELETE FROM agent_llm_").split(" ")[0]
             for sql in _delete_sqls(db)]
    # results first (its attempt_id FK is ON DELETE SET NULL), tasks last
    assert order == ["results", "events", "attempts", "requests", "tasks",
                     "model_calls"]
    assert out["deleted"]["tasks"] == 3
    assert out["deleted"]["results"] == 3
    assert out["truncated"] is False


@pytest.mark.asyncio
async def test_status_whitelist_hardcoded_and_includes_failed():
    db = _FakeCleanupDB(
        task_batches=[["t1"]],
        verify_batches=[["t1"]],
        delete_rowcounts=[1, 1, 1, 1, 1, 0],
    )
    await run_cleanup(db, retention_days=30, dry_run=False)
    task_deletes = [p for sql, p in db.conn_log if "FROM agent_llm_tasks" in sql]
    whitelist = ("succeeded", "failed", "dead_letter", "cancelled")
    for params in task_deletes:
        # whitelist lives in the SQL literal, never as a bound parameter
        assert all(status not in params for status in whitelist)
    joined = " ".join(sql for sql, _ in db.conn_log + db.sql_log)
    for status in whitelist:
        assert f"'{status}'" in joined, f"whitelist missing {status}"
    for sql, _ in db.conn_log + db.sql_log:
        if "DELETE FROM agent_llm_tasks" in sql or "SELECT id FROM agent_llm_tasks" in sql:
            assert "finished_at <" in sql


@pytest.mark.asyncio
async def test_race_with_concurrent_retry_reverify_protects_revived_task():
    """批选与批事务之间，白名单内任务可能被 /tasks/{id}/retry 复活为 queued。

    批事务内的 FOR UPDATE 复核必须把复活任务剔除——子表删除只允许触达
    复核通过的子集，否则复活任务丢失 request 快照而父行幸存（半删态）。
    """
    db = _FakeCleanupDB(
        task_batches=[["t1", "t2"]],      # selected for cleanup
        verify_batches=[["t1"]],          # t2 re-queued mid-flight: only t1 passes
        delete_rowcounts=[1, 1, 1, 1, 1, 0],
    )
    out = await run_cleanup(db, retention_days=30, dry_run=False)
    # every statement in the batch txn carries the FOR UPDATE re-verify
    verify_sqls = [sql for sql, _ in db.conn_log if "FOR UPDATE" in sql]
    assert verify_sqls and all("IN ('succeeded','failed'" in sql for sql in verify_sqls)
    batch_deletes = [(sql, p) for sql, p in db.conn_log
                     if sql.startswith("DELETE") and "model_calls" not in sql]
    for sql, params in batch_deletes:
        assert params[0] == ["t1"]  # t2's rows untouched
    assert out["deleted"]["tasks"] == 1


@pytest.mark.asyncio
async def test_domain_filter_threads_through_all_statements():
    db = _FakeCleanupDB(
        task_batches=[["t1"]],
        verify_batches=[["t1"]],
        delete_rowcounts=[1, 1, 1, 1, 1, 1],
    )
    out = await run_cleanup(db, retention_days=30, dry_run=False, knowledge_domain="d1")
    assert out["knowledge_domain"] == "d1"
    # verify SELECT + parent DELETE + batch select + model_calls all carry
    # the domain clause and the bound domain value
    for sql, params in db.conn_log:
        if "FOR UPDATE" in sql or sql.startswith("DELETE FROM agent_llm_tasks") or "model_calls" in sql:
            assert "knowledge_domain = %s" in sql, sql
            assert "d1" in params, (sql, params)
    batch_selects = [(s, p) for s, p in db.sql_log if "SELECT id FROM agent_llm_tasks" in s]
    assert batch_selects
    for sql, params in batch_selects:
        assert "knowledge_domain = %s" in sql
        assert "d1" in params
    # child deletes stay id-scoped — no domain clause leaks into them
    for sql, _ in db.conn_log:
        if sql.startswith("DELETE FROM agent_llm_results") or sql.startswith("DELETE FROM agent_llm_events") \
                or sql.startswith("DELETE FROM agent_llm_attempts") or sql.startswith("DELETE FROM agent_llm_requests"):
            assert "knowledge_domain" not in sql


@pytest.mark.asyncio
async def test_no_domain_by_default_means_all_domains():
    db = _FakeCleanupDB(counts={"tasks": 1})
    out = await run_cleanup(db, retention_days=30, dry_run=True)
    assert out["knowledge_domain"] is None
    joined = " ".join(sql for sql, _ in db.sql_log)
    assert "knowledge_domain" not in joined


@pytest.mark.asyncio
async def test_child_deletes_scoped_to_batch_ids_only():
    db = _FakeCleanupDB(
        task_batches=[["t1", "t2"]],
        verify_batches=[["t1", "t2"]],
        delete_rowcounts=[2, 2, 2, 2, 2, 0],
    )
    await run_cleanup(db, retention_days=30, dry_run=False)
    child_deletes = [(sql, p) for sql, p in db.conn_log
                     if "agent_llm_results" in sql or "agent_llm_events" in sql
                     or "agent_llm_attempts" in sql or "agent_llm_requests" in sql]
    assert child_deletes
    for sql, params in child_deletes:
        assert "= ANY(%s)" in sql
        assert params == (["t1", "t2"],)  # scoped to this batch only


@pytest.mark.asyncio
async def test_never_touches_templates_or_business_tables():
    db = _FakeCleanupDB(
        task_batches=[["t1"]],
        verify_batches=[["t1"]],
        delete_rowcounts=[1, 1, 1, 1, 1, 0],
    )
    await run_cleanup(db, retention_days=30, dry_run=False)
    everything = " ".join(sql for sql, _ in db.conn_log + db.sql_log)
    assert "prompt_templates" not in everything
    assert "kb_" not in everything.replace("task_batches", "")


@pytest.mark.asyncio
async def test_model_calls_uses_created_at_predicate_batched():
    db = _FakeCleanupDB(
        task_batches=[],  # loop A exits immediately
        delete_rowcounts=[5000, 100, 0],
    )
    out = await run_cleanup(db, retention_days=30, dry_run=False)
    mc_deletes = [sql for sql in _delete_sqls(db) if "model_calls" in sql]
    assert len(mc_deletes) == 3  # two batches + terminal empty batch
    for sql in mc_deletes:
        assert "created_at < %s" in sql
        assert "LIMIT %s" in sql
    assert out["deleted"]["model_calls"] == 5100


@pytest.mark.asyncio
async def test_truncation_on_batch_cap_reports_remaining(monkeypatch):
    monkeypatch.setattr(cleanup, "MAX_BATCHES", 2)
    db = _FakeCleanupDB(
        task_batches=[["t1"], ["t2"], ["t3"]],
        verify_batches=[["t1"], ["t2"]],  # never drains within cap
        delete_rowcounts=[1, 1, 1, 1, 1],
        counts={"tasks": 1},  # remaining recount: tasks=1, others 0
    )
    out = await run_cleanup(db, retention_days=30, dry_run=False)
    assert out["batches"] == 2
    assert out["truncated"] is True
    assert out["remaining"]["tasks"] == 1


@pytest.mark.asyncio
async def test_truncated_cleared_when_recount_is_zero(monkeypatch):
    monkeypatch.setattr(cleanup, "MAX_BATCHES", 1)
    db = _FakeCleanupDB(
        task_batches=[["t1"], ["t2"]],
        verify_batches=[["t1"]],
        delete_rowcounts=[1, 1, 1, 1, 1],
        counts={},  # recount all zero
    )
    out = await run_cleanup(db, retention_days=30, dry_run=False)
    assert out["truncated"] is False
    assert "remaining" not in out


@pytest.mark.asyncio
async def test_loops_terminate_when_no_matching_rows():
    db = _FakeCleanupDB(task_batches=[], delete_rowcounts=[0])
    out = await run_cleanup(db, retention_days=30, dry_run=False)
    assert out["deleted"] == {t: 0 for t in
                              ("tasks", "requests", "attempts", "results",
                               "events", "model_calls")}
    assert out["batches"] == 0  # empty probe batch doesn't count
    assert out["truncated"] is False


def test_request_model_bounds_and_dry_run_default():
    with pytest.raises(ValidationError):
        CleanupRequest(retention_days=0)
    with pytest.raises(ValidationError):
        CleanupRequest(retention_days=3661)
    default = CleanupRequest()
    assert default.retention_days == 30
    assert default.dry_run is True  # omitted field can never delete


@pytest.mark.asyncio
def _cleanup_request(role=None, secret=None, ivs="ivs"):
    """Build a Request-like namespace for the fail-closed auth matrix."""
    headers = {}
    if role is not None:
        headers["x-kb-role"] = role
    if secret is not None:
        headers["x-internal-auth"] = secret
    return SimpleNamespace(
        headers=headers,
        app=SimpleNamespace(
            state=SimpleNamespace(db=object(), internal_verify_secret=ivs)
        ),
    )


@pytest.mark.asyncio
async def test_route_fail_closed_auth_matrix():
    """Direct calls without BOTH the internal secret and admin role are 403;
    an unconfigured secret on the service side is 503 — never open."""
    from fastapi import HTTPException

    from llm_service.api import admin as admin_api

    async def expect(status, request):
        with pytest.raises(HTTPException) as exc_info:
            await admin_api.cleanup_tasks(CleanupRequest(), request)
        assert exc_info.value.status_code == status

    await expect(403, _cleanup_request())                            # no headers at all
    await expect(403, _cleanup_request(role="admin"))                # role w/o secret
    await expect(403, _cleanup_request(secret="ivs", role="member"))  # secret w/o admin
    await expect(403, _cleanup_request(secret="wrong", role="admin"))  # forged secret
    await expect(503, _cleanup_request(role="admin", secret="ivs", ivs=""))  # svc unset


@pytest.mark.asyncio
async def test_route_returns_envelope_and_passes_params(monkeypatch):
    from llm_service.api import admin as admin_api

    captured = {}

    async def _fake_run(db, *, retention_days, dry_run, knowledge_domain=None):
        captured.update(retention_days=retention_days, dry_run=dry_run,
                        knowledge_domain=knowledge_domain)
        return {"dry_run": dry_run, "truncated": False, "deleted": {"tasks": 0}}

    # cleanup_tasks imports run_cleanup lazily from its source module
    monkeypatch.setattr("llm_service.runtime.cleanup.run_cleanup", _fake_run)
    body = CleanupRequest(retention_days=7, dry_run=False, knowledge_domain="d1")
    request = _cleanup_request(role="admin", secret="ivs")
    out = await admin_api.cleanup_tasks(body, request)
    assert out["success"] is True
    assert out["data"]["deleted"]["tasks"] == 0
    assert captured == {"retention_days": 7, "dry_run": False, "knowledge_domain": "d1"}
