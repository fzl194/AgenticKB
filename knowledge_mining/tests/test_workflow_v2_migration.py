from __future__ import annotations

from copy import deepcopy

import pytest

def _v1_graph() -> dict:
    return {
        "schemaVersion": "1.0",
        "nodes": [
            {"nodeId": "input_ingest", "operatorType": "input_ingest", "operatorVersion": "1", "params": {}, "ui": {"x": 0, "y": 0}},
            {"nodeId": "parse_segment", "operatorType": "parse_segment", "operatorVersion": "1", "params": {"maxSegmentTokens": 700}, "ui": {"x": 200, "y": 0}},
            {"nodeId": "asset_persist", "operatorType": "asset_persist", "operatorVersion": "1", "params": {}, "ui": {"x": 400, "y": 0}},
            {"nodeId": "mining_finalize", "operatorType": "mining_finalize", "operatorVersion": "1", "params": {}, "ui": {"x": 600, "y": 0}},
        ],
        "edges": [
            {"fromNode": "input_ingest", "fromSlot": "rawFiles", "toNode": "parse_segment", "toSlot": "rawFiles"},
            {"fromNode": "parse_segment", "fromSlot": "documents", "toNode": "asset_persist", "toSlot": "documents"},
            {"fromNode": "asset_persist", "fromSlot": "finalizeInput", "toNode": "mining_finalize", "toSlot": "finalizeInput"},
        ],
        "output": {"nodeId": "mining_finalize", "slot": "result"},
    }


def test_upgrade_graph_replaces_legacy_parse_segment_with_v2_fixed_head() -> None:
    from knowledge_mining.mining.workflow.v2_migration import upgrade_graph_to_v2

    graph = _v1_graph()
    original = deepcopy(graph)

    upgraded = upgrade_graph_to_v2(graph)

    assert graph == original
    assert upgraded["schemaVersion"] == "2.0"
    assert {
        node["operatorType"] for node in upgraded["nodes"]
    } >= {"input_ingest", "document_parse", "segment_compile", "asset_persist"}
    assert "parse_segment" not in {
        node["operatorType"] for node in upgraded["nodes"]
    }
    parse_node = next(
        node for node in upgraded["nodes"]
        if node["operatorType"] == "document_parse"
    )
    compile_node = next(
        node for node in upgraded["nodes"]
        if node["operatorType"] == "segment_compile"
    )
    assert parse_node["nodeId"] == "parse_segment"
    assert compile_node["nodeId"] == "parse_segment_segment_compile"
    assert any(
        edge == {
            "fromNode": parse_node["nodeId"],
            "fromSlot": "documents",
            "toNode": compile_node["nodeId"],
            "toSlot": "documents",
        }
        for edge in upgraded["edges"]
    )
    assert any(
        edge["fromNode"] == compile_node["nodeId"]
        and edge["toNode"] == "asset_persist"
        for edge in upgraded["edges"]
    )


def test_upgrade_graph_is_idempotent_for_v2_graphs() -> None:
    from knowledge_mining.mining.workflow.v2_migration import upgrade_graph_to_v2

    once = upgrade_graph_to_v2(_v1_graph())

    assert upgrade_graph_to_v2(once) == once


def test_v2_graph_with_old_segment_defaults_refreshed_to_new() -> None:
    """已 v2 图烤死的旧默认档位（512/64/rows）→ 新默认（2048/512/whole）.

    启动自愈经 upgrade_active_workflows_to_v2 走本函数；只刷"恰好等于
    旧默认"的键，用户显式选的其它值（如 700）不动。
    """
    from knowledge_mining.mining.workflow.v2_migration import upgrade_graph_to_v2

    once = upgrade_graph_to_v2(_v1_graph())  # maxSegmentTokens=700 保留为 700
    compile_node = next(
        n for n in once["nodes"] if n["operatorType"] == "segment_compile"
    )
    compile_node["params"] = {
        "maxTokens": 512, "minTokens": 64, "tableView": "rows",
        "mergeAdjacentParagraphs": True, "injectHeadingContext": True,
        "includeFigureCaptions": True,
    }
    another = deepcopy(once)
    another["nodes"] = [
        {**n, "params": {**n["params"], "maxTokens": 700}}
        if n["operatorType"] == "segment_compile" else n
        for n in another["nodes"]
    ]

    refreshed = upgrade_graph_to_v2(once)
    params = next(
        n for n in refreshed["nodes"] if n["operatorType"] == "segment_compile"
    )["params"]
    assert params["maxTokens"] == 2048
    assert params["minTokens"] == 512
    assert params["tableView"] == "whole"

    # 显式非默认值不动 + 刷新幂等（二次运行不再变化）。
    kept = upgrade_graph_to_v2(another)
    kept_params = next(
        n for n in kept["nodes"] if n["operatorType"] == "segment_compile"
    )["params"]
    assert kept_params["maxTokens"] == 700
    assert kept_params["minTokens"] == 512  # 64 是旧默认，仍刷新
    assert upgrade_graph_to_v2(refreshed) == refreshed


def test_upgrade_graph_rejects_mixed_v1_and_v2_parse_operators() -> None:
    from knowledge_mining.mining.workflow.v2_migration import (
        WorkflowV2MigrationError,
        upgrade_graph_to_v2,
    )

    graph = _v1_graph()
    graph["nodes"].append({
        "nodeId": "document_parse",
        "operatorType": "document_parse",
        "operatorVersion": "1",
        "params": {},
        "ui": {"x": 0, "y": 0},
    })

    with pytest.raises(WorkflowV2MigrationError, match="mixed"):
        upgrade_graph_to_v2(graph)
