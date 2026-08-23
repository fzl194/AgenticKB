from __future__ import annotations

import sqlite3

import pytest


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE knowledge_bases (id TEXT PRIMARY KEY, name TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE asset_documents (id TEXT PRIMARY KEY, kb_id TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE asset_storage_objects (id TEXT PRIMARY KEY, object_key TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE mining_runs (id TEXT PRIMARY KEY, execution_engine TEXT, status TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE mining_run_documents (id TEXT PRIMARY KEY, run_id TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE mining_run_stage_events (id TEXT PRIMARY KEY, run_id TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE mining_workflow_node_events (id TEXT PRIMARY KEY, run_id TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO knowledge_bases VALUES ('kb-1', 'retain')")
    conn.execute("INSERT INTO asset_documents VALUES ('doc-1', 'kb-1')")
    conn.execute("INSERT INTO asset_storage_objects VALUES ('obj-1', 'source/retain')")
    conn.executemany(
        "INSERT INTO mining_runs VALUES (?, ?, ?)",
        [("legacy-complete", "legacy", "completed"), ("workflow", "workflow", "completed")],
    )
    conn.executemany(
        "INSERT INTO mining_run_documents VALUES (?, ?)",
        [("legacy-doc", "legacy-complete"), ("workflow-doc", "workflow")],
    )
    conn.executemany(
        "INSERT INTO mining_run_stage_events VALUES (?, ?)",
        [("legacy-stage", "legacy-complete"), ("workflow-stage", "workflow")],
    )
    conn.executemany(
        "INSERT INTO mining_workflow_node_events VALUES (?, ?)",
        [("legacy-node", "legacy-complete"), ("workflow-node", "workflow")],
    )
    return conn


def _service(conn: sqlite3.Connection):
    from knowledge_mining.mining.workflow.v2_cutover import LegacyMiningOutputCutoverService

    return LegacyMiningOutputCutoverService(conn, dialect="sqlite")


def test_plan_is_read_only_and_reports_only_legacy_mining_output_counts() -> None:
    conn = _connection()

    plan = _service(conn).plan()

    assert plan.legacy_run_count == 2
    assert plan.counts == {
        "mining_workflow_node_events": 2,
        "mining_run_stage_events": 2,
        "mining_run_documents": 2,
        "mining_runs": 2,
    }
    assert plan.total_rows == 8
    assert plan.is_executable is True
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'mining_v2_cutover_audits'"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM mining_runs").fetchone()[0] == 2


def test_execution_requires_the_exact_plan_confirmation_token() -> None:
    conn = _connection()
    service = _service(conn)

    with pytest.raises(ValueError, match="explicit confirmation"):
        service.execute("DELETE_LEGACY_MINING_OUTPUTS")

    assert conn.execute("SELECT COUNT(*) FROM mining_runs").fetchone()[0] == 2


def test_execution_refuses_a_confirmation_from_a_stale_plan() -> None:
    conn = _connection()
    service = _service(conn)
    plan = service.plan()
    conn.execute("INSERT INTO mining_runs VALUES ('new-legacy', 'legacy', 'completed')")

    with pytest.raises(ValueError, match="does not match"):
        service.execute(plan.confirmation_token)

    assert conn.execute("SELECT COUNT(*) FROM mining_runs").fetchone()[0] == 3


def test_execution_deletes_only_legacy_mining_output_and_records_counts() -> None:
    conn = _connection()
    service = _service(conn)
    plan = service.plan()

    result = service.execute(plan.confirmation_token)

    assert result.deleted_counts == plan.counts
    assert conn.execute("SELECT id FROM mining_runs ORDER BY id").fetchall() == []
    assert conn.execute("SELECT id FROM mining_run_documents").fetchall() == []
    assert conn.execute("SELECT id FROM mining_run_stage_events").fetchall() == []
    assert conn.execute("SELECT id FROM mining_workflow_node_events").fetchall() == []
    assert conn.execute("SELECT id FROM knowledge_bases").fetchall() == [("kb-1",)]
    assert conn.execute("SELECT id FROM asset_documents").fetchall() == [("doc-1",)]
    assert conn.execute("SELECT id FROM asset_storage_objects").fetchall() == [("obj-1",)]
    assert conn.execute(
        "SELECT legacy_run_count, deleted_row_count FROM mining_v2_cutover_audits"
    ).fetchall() == [(2, 8)]


def test_execution_refuses_when_legacy_run_is_not_terminal() -> None:
    conn = _connection()
    conn.execute("UPDATE mining_runs SET status = 'running' WHERE id = 'legacy-complete'")
    service = _service(conn)
    plan = service.plan()

    assert plan.active_legacy_run_count == 1
    assert plan.is_executable is False
    with pytest.raises(RuntimeError, match="active legacy mining runs"):
        service.execute(plan.confirmation_token)

    assert conn.execute("SELECT COUNT(*) FROM mining_runs").fetchone()[0] == 2


def test_legacy_database_without_execution_engine_is_treated_as_legacy() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE mining_runs (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    conn.execute("CREATE TABLE mining_run_documents (id TEXT PRIMARY KEY, run_id TEXT NOT NULL)")
    conn.execute("CREATE TABLE mining_run_stage_events (id TEXT PRIMARY KEY, run_id TEXT NOT NULL)")
    conn.execute("INSERT INTO mining_runs VALUES ('old-run', 'completed')")

    plan = _service(conn).plan()

    assert plan.counts == {
        "mining_run_stage_events": 0,
        "mining_run_documents": 0,
        "mining_runs": 1,
    }
