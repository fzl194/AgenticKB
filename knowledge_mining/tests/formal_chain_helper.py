"""批次8 M0 测试共享助手：正式固定链图（模板已清空，测试显式传图）。

链 = input_ingest → document_parse → segment_compile → asset_persist →
mining_finalize；embedding 属可选算子（M2 前无上游能力提供者），
需要时用 with_embedding 变体。
"""
from __future__ import annotations

from knowledge_mining.mining.workflow.graph import (
    EdgeDef,
    NodeDef,
    OutputDef,
    WorkflowGraph,
)


def formal_chain_workflow_graph(*, with_embedding: bool = False) -> WorkflowGraph:
    types = ["input_ingest", "document_parse", "segment_compile"]
    edges = [
        EdgeDef("input_ingest", "rawFiles", "document_parse", "rawFiles"),
        EdgeDef("document_parse", "documents", "segment_compile", "documents"),
    ]
    if with_embedding:
        types.append("embedding")
        edges.append(
            EdgeDef("segment_compile", "documents", "embedding", "documents")
        )
        edges.append(
            EdgeDef("embedding", "documents", "asset_persist", "discourseAssets")
        )
    else:
        edges.append(
            EdgeDef("segment_compile", "documents", "asset_persist", "documents")
        )
    types.extend(["asset_persist", "mining_finalize"])
    edges.append(
        EdgeDef("asset_persist", "finalizeInput", "mining_finalize", "finalizeInput")
    )
    return WorkflowGraph(
        schema_version="2.0",
        nodes=tuple(
            NodeDef(node_id=t, operator_type=t, params={}) for t in types
        ),
        edges=tuple(edges),
        output=OutputDef("mining_finalize", "result"),
    )


def formal_chain_graph_dict(*, with_embedding: bool = False) -> dict:
    return formal_chain_workflow_graph(with_embedding=with_embedding).to_dict()
