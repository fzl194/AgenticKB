"""31 号 Wave 2：run 展示必须来自冻结 workflow manifest。"""
from __future__ import annotations


def _manifest():
    return {
        "workflowId": "system-hybrid-assets",
        "workflowName": "标准混合资产",
        "templateKey": "hybrid_assets",
        "workflowVersion": 3,
        "graphHash": "hash",
        "schemaVersion": "2.0",
        "catalogVersion": "1",
        "graph": {"nodes": [], "edges": []},
        "nodes": [
            {"nodeId": "ingest", "type": "input_ingest"},
            {"nodeId": "parse", "type": "document_parse"},
            {"nodeId": "embed", "type": "embedding"},
            {"nodeId": "final", "type": "mining_finalize"},
        ],
        "executionPlan": {
            "inputOrder": ["ingest"],
            "documentOrder": ["parse", "embed"],
            "globalOrder": ["final"],
        },
    }


def test_frozen_workflow_summary_exposes_frozen_name() -> None:
    from knowledge_mining.mining.api.routes.runs import _frozen_workflow_summary

    summary = _frozen_workflow_summary({
        "execution_engine": "workflow",
        "workflow_manifest_json": _manifest(),
    })
    assert summary["name"] == "标准混合资产"
    assert summary["id"] == "system-hybrid-assets"
    assert summary["template_key"] == "hybrid_assets"


def test_workflow_progress_uses_manifest_nodes_not_legacy_tail() -> None:
    from knowledge_mining.mining.api.routes.runs import _workflow_progress

    events = [
        {"node_id": "ingest", "operator_type": "input_ingest",
         "run_document_id": None, "status": "completed", "attempt_no": 1,
         "started_at": "2026-01-01T00:00:00Z"},
        {"node_id": "parse", "operator_type": "document_parse",
         "run_document_id": "d1", "status": "completed", "attempt_no": 1,
         "started_at": "2026-01-01T00:00:01Z"},
        {"node_id": "embed", "operator_type": "embedding",
         "run_document_id": "d1", "status": "started", "attempt_no": 1,
         "started_at": "2026-01-01T00:00:02Z"},
    ]
    progress = _workflow_progress(
        manifest=_manifest(), node_rows=events,
        executable_documents=2, run_status="running",
    )
    # Expected units: input 1 + document(2 nodes * 2 docs) + global 1 = 6.
    assert progress["expected_units"] == 6
    assert progress["completed_units"] == 2
    assert progress["progress_percent"] == 33.3
    assert progress["current_stage"] == "embedding"
    assert "graph_write" not in progress["stage_summary"]


def test_terminal_workflow_progress_is_100_even_for_skipped_nodes() -> None:
    from knowledge_mining.mining.api.routes.runs import _workflow_progress

    progress = _workflow_progress(
        manifest=_manifest(), node_rows=[],
        executable_documents=2, run_status="completed",
    )
    assert progress["progress_percent"] == 100.0


def test_workflow_progress_excludes_preflight_reused_documents() -> None:
    from knowledge_mining.mining.api.routes.runs import _workflow_progress

    progress = _workflow_progress(
        manifest=_manifest(), node_rows=[], executable_documents=1,
        run_status="running",
    )
    assert progress["expected_units"] == 4  # 1 input + 2 doc + 1 global


def test_paused_is_current_but_interrupted_is_not_fake_100_percent() -> None:
    from knowledge_mining.mining.api.routes.runs import _workflow_progress

    progress = _workflow_progress(
        manifest=_manifest(),
        node_rows=[{
            "node_id": "embed", "operator_type": "embedding",
            "run_document_id": "d1", "status": "paused", "attempt_no": 1,
            "started_at": "2026-01-01T00:00:02Z",
        }],
        executable_documents=1,
        run_status="interrupted",
    )
    assert progress["current_stage"] == "embedding"
    assert progress["completed_units"] == 0
    assert progress["progress_percent"] == 0.0
