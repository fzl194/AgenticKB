from knowledge_mining.mining.infra.db import MiningRuntimeDB


def _db(rowcount: int = 1):
    db = object.__new__(MiningRuntimeDB)
    db.calls = []
    def run(sql, params, *, fetch):
        db.calls.append((sql, params, fetch))
        return rowcount
    db._run = run
    return db


def test_claim_is_atomic_and_only_accepts_unowned_or_expired_run():
    db = _db()
    assert db.claim_run("r1", "d1", "worker-a") is True
    sql, params, fetch = db.calls[0]
    assert "pg_try_advisory_xact_lock" in sql
    assert "AND NOT EXISTS" in sql
    assert "worker_id IS NULL OR lease_until IS NULL OR lease_until < NOW()" in sql
    assert "attempt_no = attempt_no + 1" in sql
    assert params == (
        "d1", "worker-a", 300, ["queued", "running"], "r1", "d1", "d1", "r1",
    ) and fetch == "rowcount"


def test_claim_allows_resume_statuses_when_requested():
    """resume 认领必须覆盖 interrupted/failed/awaiting_review（与 resume_running 对齐）."""
    db = _db()
    statuses = ("running", "interrupted", "failed", "awaiting_review")
    db.claim_run("r1", "d1", "worker-a", allowed_statuses=statuses)
    sql, params, _, = db.calls[0]
    assert "status = ANY(%s)" in sql
    # allowed_statuses 随参数下发，默认路径不受影响
    assert params == ("d1", "worker-a", 300, list(statuses), "r1", "d1", "d1", "r1")


def test_claim_default_statuses_still_queued_running_only():
    db = _db(0)
    assert db.claim_run("r1", "d1", "worker-a") is False
    _, params, _, = db.calls[0]
    assert params == ("d1", "worker-a", 300, ["queued", "running"], "r1", "d1", "d1", "r1")


def test_renew_is_fenced_by_worker_only_not_status():
    """续租只看 worker_id 归属；resume 认领后状态仍是 interrupted 时也必须能续租."""
    db = _db()
    assert db.renew_run_lease("r1", "worker-a") is True
    sql, params, _, = db.calls[0]
    assert "worker_id = %s" in sql
    assert "status" not in sql
    assert params == (300, "r1", "worker-a")


def test_renew_and_release_require_current_owner():
    db = _db(0)
    assert db.renew_run_lease("r1", "other") is False
    assert db.release_run_lease("r1", "other") is False
    assert all("worker_id = %s" in call[0] for call in db.calls)


# -- P07-S2 修复：workflow 引擎接线 -------------------------------------------------

def test_workflow_claim_uses_resume_statuses_for_resume_action():
    from knowledge_mining.mining.jobs.run import _claim_statuses_for_action

    assert _claim_statuses_for_action("execute") == ("queued", "running")
    assert _claim_statuses_for_action("resume") == (
        "running", "interrupted", "failed", "awaiting_review",
    )
    assert _claim_statuses_for_action("publish") is None  # publish 不认领
