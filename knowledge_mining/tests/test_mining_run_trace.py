from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.api.routes import runs


class Cursor:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class TraceConnection:
    def __init__(self, run):
        self.run = run

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        if "SELECT id, domain, status, kb_id FROM mining_runs" in normalized:
            return Cursor(row={
                "id": self.run["id"],
                "domain": self.run["domain"],
                "status": self.run["status"],
                "kb_id": self.run.get("kb_id"),
            })
        if "FROM mining_runs WHERE id = %s" in normalized:
            return Cursor(row=dict(self.run))
        if "FROM mining_workflow_node_events" in normalized:
            return Cursor(rows=[{
                "id": "node-event-1",
                "run_id": self.run["id"],
                "run_document_id": "doc-1",
                "node_id": "parse_segment",
                "operator_type": "parse_segment",
                "operator_version": "1",
                "status": "completed",
                "attempt_no": 1,
                "started_at": "2026-07-24T00:00:00Z",
                "finished_at": "2026-07-24T00:00:01Z",
                "duration_ms": 1000,
                "input_summary_json": {},
                "output_summary_json": {},
                "error_code": None,
                "error_message": None,
                "metadata_json": {"warnings": [{"code": "fallback", "message": "used cache"}]},
            }])
        if "FROM mining_run_stage_events" in normalized:
            return Cursor(rows=[{
                "id": "stage-1", "stage": "ingest", "status": "completed"
            }])
        if "FROM mining_run_documents" in normalized and "COUNT" not in normalized:
            return Cursor(rows=[{
                "id": "doc-1",
                "document_key": "doc:/a.md",
                "action": "NEW",
                "status": "committed",
                "document_id": "asset-doc-1",
                "document_snapshot_id": "snapshot-1",
                "error_message": None,
            }])
        counts = {
            "ontology_candidates WHERE domain_id": 2,
            "ontology_candidates WHERE domain_id = %s AND source": 3,
            "asset_segment_entity_mentions": 1,
            "ontology_entities": 7,
            "ontology_entity_relations": 8,
        }
        for marker, value in counts.items():
            if marker in normalized:
                return Cursor(row={"n": value})
        raise AssertionError(normalized)


class Pool:
    def __init__(self, run):
        self.connection_value = TraceConnection(run)

    @asynccontextmanager
    async def connection(self):
        yield self.connection_value


def request_for(run):
    pool = Pool(run)
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                domain_pools=SimpleNamespace(
                    async_pool=lambda domain: _async_value(pool)
                )
            )
        )
    )


async def _async_value(value):
    return value


def workflow_run():
    manifest = {
        "schemaVersion": "1.0",
        "catalogVersion": "1",
        "workflowId": "workflow-a",
        "workflowVersion": 4,
        "graphHash": "graph-hash",
        "graph": {
            "schemaVersion": "1.0",
            "nodes": [{
                "nodeId": "parse_segment",
                "operatorType": "parse_segment",
                "operatorVersion": "1",
                "params": {},
                "ui": {},
            }],
            "edges": [],
            "output": {"nodeId": "parse_segment", "slot": "documents"},
        },
        "nodes": [],
        "edges": [],
        "executionPlan": {"requiredCompletion": ["finalized"]},
    }
    return {
        "id": "run-1",
        "domain": "plant-a",
        "status": "running",
        "current_stage": "mining",
        "subloop_stage": None,
        "ontology_version_id": "ontology-1",
        "total_documents": 1,
        "committed_count": 1,
        "new_count": 1,
        "updated_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "started_at": "2026-07-24T00:00:00Z",
        "finished_at": None,
        "execution_engine": "workflow",
        "workflow_id": "workflow-a",
        "workflow_version": 4,
        "workflow_version_id": "workflow-version-4",
        "workflow_graph_hash": "graph-hash",
        "workflow_manifest_json": manifest,
        "active_node_id": "parse_segment",
        "active_operator_type": "parse_segment",
        "pause_step": None,
        "build_id": None,
    }


@pytest.mark.asyncio
async def test_workflow_trace_comes_only_from_frozen_run_and_domain_events(monkeypatch):
    monkeypatch.setattr(runs, "require_domain", lambda value: value)
    run = workflow_run()

    body = await runs.get_run_trace(
        run["id"], request_for(run), domain="plant-a"
    )

    assert body["workflow"]["version"] == 4
    assert body["workflow"]["graph_hash"] == "graph-hash"
    assert body["workflow"]["graph"] == run["workflow_manifest_json"]["graph"]
    assert body["node_events"][0]["attempt_no"] == 1
    assert body["active_node_id"] == "parse_segment"
    assert body["warnings"] == [{
        "node_id": "parse_segment",
        "attempt_no": 1,
        "code": "fallback",
        "message": "used cache",
    }]
    assert body["stage_events"][0]["stage"] == "ingest"
    assert body["documents"][0]["document_key"] == "doc:/a.md"
    assert body["asset_counts"] == {"entities": 7, "relations": 8}


@pytest.mark.asyncio
async def test_legacy_trace_retains_fields_and_has_null_workflow(monkeypatch):
    monkeypatch.setattr(runs, "require_domain", lambda value: value)
    run = workflow_run()
    run.update(
        id="legacy-1",
        execution_engine="legacy",
        workflow_id=None,
        workflow_version=None,
        workflow_version_id=None,
        workflow_graph_hash=None,
        workflow_manifest_json=None,
        active_node_id=None,
        active_operator_type=None,
    )

    body = await runs.get_run_trace(
        run["id"], request_for(run), domain="plant-a"
    )

    assert body["workflow"] is None
    assert body["node_events"] == []
    assert "stage_events" in body
    assert "asset_counts" in body
