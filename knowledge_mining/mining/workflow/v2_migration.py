"""Deterministic conversion of persisted workflow graphs to the v2 head.

The product no longer exposes a v1 execution path.  Existing paradigms retain
their identity, name, and downstream branches; this module changes only the
legacy parse-and-segment head into the explicit v2 pair.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


class WorkflowV2MigrationError(ValueError):
    """A stored graph cannot be safely converted without human intervention."""


_LEGACY_PARSE = "parse_segment"
_DOCUMENT_PARSE = "document_parse"
_SEGMENT_COMPILE = "segment_compile"

#: 切片档位默认值换代（v2 编译器，2026-08）：启动自愈时把"恰好等于
#: 旧默认值"的 segment_compile 参数刷成新默认（大上下文窗口尺度 +
#: 表格默认整表）。两种命中形态：显式等于旧默认，或**键缺失**（草稿
#: 依赖默认值时，发布版 manifest 在旧时代发布即烤死旧默认——空参数
#: 必须显式补新默认才能触发重发版）。只改这三个键——幂等不动用户显
#: 式选择的其它值。
_OLD_SEGMENT_DEFAULTS = {"maxTokens": 512, "minTokens": 64, "tableView": "rows"}
_NEW_SEGMENT_DEFAULTS = {"maxTokens": 2048, "minTokens": 512, "tableView": "whole"}


def upgrade_graph_to_v2(graph: dict[str, Any]) -> dict[str, Any]:
    """Return an immutable-style v2 upgrade of one persisted workflow graph.

    The legacy node keeps its id after becoming ``document_parse`` so existing
    inbound edges and UI selection remain stable.  A deterministic compile node
    is inserted immediately after it and owns all former outbound edges.
    """
    upgraded = deepcopy(graph)
    nodes = list(upgraded.get("nodes") or ())
    types = {str(node.get("operatorType") or "") for node in nodes}
    has_legacy = _LEGACY_PARSE in types
    has_v2 = bool({_DOCUMENT_PARSE, _SEGMENT_COMPILE} & types)

    if has_legacy and has_v2:
        raise WorkflowV2MigrationError(
            "mixed v1 and v2 parse operators cannot be upgraded automatically"
        )
    if has_v2:
        upgraded["schemaVersion"] = "2.0"
        return _refresh_segment_defaults(upgraded)
    if not has_legacy:
        raise WorkflowV2MigrationError(
            "workflow has no parse operator and cannot become a v2 paradigm"
        )

    legacy_nodes = [node for node in nodes if node.get("operatorType") == _LEGACY_PARSE]
    if len(legacy_nodes) != 1:
        raise WorkflowV2MigrationError(
            "workflow must contain exactly one legacy parse_segment operator"
        )
    legacy = legacy_nodes[0]
    legacy_id = str(legacy.get("nodeId") or "")
    if not legacy_id:
        raise WorkflowV2MigrationError("legacy parse_segment node has no nodeId")
    compile_id = f"{legacy_id}_{_SEGMENT_COMPILE}"
    if any(str(node.get("nodeId") or "") == compile_id for node in nodes):
        raise WorkflowV2MigrationError(f"reserved node id already exists: {compile_id}")

    legacy_params = dict(legacy.get("params") or {})
    parse_node = {
        **legacy,
        "operatorType": _DOCUMENT_PARSE,
        "operatorVersion": "1",
        "params": {
            "qualityProfile": "default",
            "maxBackendAttempts": 3,
        },
    }
    ui = dict(legacy.get("ui") or {})
    compile_node = {
        "nodeId": compile_id,
        "operatorType": _SEGMENT_COMPILE,
        "operatorVersion": "1",
        "params": _segment_params(legacy_params),
        "ui": {
            **ui,
            "x": int(ui.get("x") or 0) + 200,
        },
    }
    upgraded["nodes"] = [
        parse_node if node.get("nodeId") == legacy_id else node
        for node in nodes
    ] + [compile_node]

    edges: list[dict[str, Any]] = []
    for edge in upgraded.get("edges") or ():
        copied = dict(edge)
        if copied.get("fromNode") == legacy_id:
            copied["fromNode"] = compile_id
        edges.append(copied)
    edges.append({
        "fromNode": legacy_id,
        "fromSlot": "documents",
        "toNode": compile_id,
        "toSlot": "documents",
    })
    upgraded["edges"] = edges
    upgraded["schemaVersion"] = "2.0"
    return upgraded


def _segment_params(legacy: dict[str, Any]) -> dict[str, Any]:
    return {
        "maxTokens": int(legacy.get("maxSegmentTokens") or 2048),
        "minTokens": int(legacy.get("minSegmentTokens") or 512),
        "mergeAdjacentParagraphs": bool(legacy.get("mergeSmallSegments", True)),
        "injectHeadingContext": legacy.get("structuralContextMode", "breadcrumb")
        != "off",
        "tableView": "whole",
        "includeFigureCaptions": bool(legacy.get("enableImageCaption", False)),
    }


def _refresh_segment_defaults(graph: dict[str, Any]) -> dict[str, Any]:
    """已 v2 图：segment_compile 参数刷成新默认档位.

    命中两种形态：值等于旧默认（显式烤死）、或键缺失（草稿空参数，
    但已发布 manifest 在旧时代发布时烤死了旧默认——补显式新默认才能
    触发重发版让运行吃到新档位）。

    幂等固定点：补齐/刷新后二次运行不再命中 → 图不再变化，启动自愈的
    "图未变则不重发版"判断保持成立。
    """
    for node in graph.get("nodes") or ():
        if node.get("operatorType") != _SEGMENT_COMPILE:
            continue
        params = dict(node.get("params") or {})
        for key, new_value in _NEW_SEGMENT_DEFAULTS.items():
            if key not in params or params[key] == _OLD_SEGMENT_DEFAULTS[key]:
                params[key] = new_value
        node["params"] = params
    return graph


__all__ = ["WorkflowV2MigrationError", "upgrade_graph_to_v2"]
