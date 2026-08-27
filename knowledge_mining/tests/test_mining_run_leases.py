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
    assert params == ("d1", "worker-a", 300, "r1", "d1", "d1", "r1") and fetch == "rowcount"


def test_renew_and_release_require_current_owner():
    db = _db(0)
    assert db.renew_run_lease("r1", "other") is False
    assert db.release_run_lease("r1", "other") is False
    assert all("worker_id = %s" in call[0] for call in db.calls)
