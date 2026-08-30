"""批次8 M6（24 号 §8）：4 套官方挖掘预置模板.

- lexical_assets：轻量关键词资产（无 embedding 服务场景）；
- hybrid_assets：标准混合资产（**官方默认**，system-hybrid-assets）；
- query_alias_assets：标准链 + query_expansion（embedding 前，实验）；
- longdoc_assets：标准链 + hierarchical_summary（embedding 前，实验）。

旧 7 类模板（minimal/fast_retrieval/…/full）已在 M0 退役；small-to-big/
table/code/结构导航是标准资产契约的一部分，不是独立模板。
"""
from __future__ import annotations

from .graph import EdgeDef, NodeDef, OutputDef, WorkflowGraph

# 节点坐标（画布布局）
_POSITIONS = {
    "input_ingest": (40, 260),
    "document_parse": (180, 260),
    "segment_compile": (380, 260),
    "retrieval_unit_project": (580, 260),
    "query_expansion_generate": (760, 140),
    "hierarchical_summary_generate": (760, 380),
    "embedding": (960, 260),
    "asset_persist": (1160, 260),
    "mining_finalize": (1360, 260),
}


def _node(operator_type: str, params: dict | None = None) -> NodeDef:
    x, y = _POSITIONS[operator_type]
    return NodeDef(
        node_id=operator_type,
        operator_type=operator_type,
        params=params or {},
        ui={"x": x, "y": y},
    )


def _template(
    *,
    with_embedding: bool,
    with_query_expansion: bool = False,
    with_summary: bool = False,
) -> WorkflowGraph:
    # 表格行拆分（tableView=both）是标准资产契约的一部分（24 号 §8：
    # "table/code/list/结构导航不是独立范式，是标准资产契约的一部分"）——
    # 四套预置全部显式开启，table_row 表示与 table_cells 由默认链产出。
    node_params: dict[str, dict] = {
        "segment_compile": {"tableView": "both"},
    }
    if with_embedding:
        # 标准混合家族追加章节表示（§5.4 矩阵 section 默认 FTS/dense 开）
        node_params["retrieval_unit_project"] = {"includeSections": True}

    types = ["input_ingest", "document_parse", "segment_compile",
             "retrieval_unit_project"]
    if with_query_expansion:
        types.append("query_expansion_generate")
    if with_summary:
        types.append("hierarchical_summary_generate")
    if with_embedding:
        types.append("embedding")
    types.extend(["asset_persist", "mining_finalize"])

    edges = [
        EdgeDef("input_ingest", "rawFiles", "document_parse", "rawFiles"),
        EdgeDef("document_parse", "documents", "segment_compile", "documents"),
        EdgeDef("segment_compile", "documents",
                "retrieval_unit_project", "documents"),
    ]
    head = "retrieval_unit_project"
    for optional in ("query_expansion_generate", "hierarchical_summary_generate"):
        if optional in types:
            edges.append(EdgeDef(head, "documents", optional, "documents"))
            head = optional
    if with_embedding:
        edges.append(EdgeDef(head, "documents", "embedding", "documents"))
        edges.append(EdgeDef("embedding", "documents",
                             "asset_persist", "discourseAssets"))
    else:
        edges.append(EdgeDef(head, "documents", "asset_persist", "discourseAssets"))
    edges.append(EdgeDef("retrieval_unit_project", "documents",
                         "asset_persist", "documents"))
    edges.append(EdgeDef("asset_persist", "finalizeInput",
                         "mining_finalize", "finalizeInput"))

    return WorkflowGraph(
        schema_version="2.0",
        nodes=tuple(_node(t, node_params.get(t)) for t in types),
        edges=tuple(edges),
        output=OutputDef("mining_finalize", "result"),
    )


_BUILTIN_TEMPLATES = {
    "lexical_assets": _template(with_embedding=False),
    "hybrid_assets": _template(with_embedding=True),
    "query_alias_assets": _template(
        with_embedding=True, with_query_expansion=True,
    ),
    "longdoc_assets": _template(with_embedding=True, with_summary=True),
}


def builtin_templates() -> dict[str, WorkflowGraph]:
    return dict(_BUILTIN_TEMPLATES)


def builtin_templates_v2() -> dict[str, WorkflowGraph]:
    """Compatibility import name for the only supported template generation."""
    return builtin_templates()
