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
        return upgraded
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
        "maxTokens": int(legacy.get("maxSegmentTokens") or 512),
        "minTokens": int(legacy.get("minSegmentTokens") or 64),
        "mergeAdjacentParagraphs": bool(legacy.get("mergeSmallSegments", True)),
        "injectHeadingContext": legacy.get("structuralContextMode", "breadcrumb")
        != "off",
        "tableView": "rows",
        "includeFigureCaptions": bool(legacy.get("enableImageCaption", False)),
    }


__all__ = ["WorkflowV2MigrationError", "upgrade_graph_to_v2"]
