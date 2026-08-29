from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .core import MiningOperatorDef
from .graph import EdgeDef, WorkflowGraph

# 批次8 M0（24 号 §5.12-§5.16）：实体/本体/图谱研究算子不再自动注入正式图。
# 全局链只剩 asset_persist → mining_finalize。
_GLOBAL_CHAIN_TYPES = {"asset_persist", "mining_finalize"}


def required_protected_types(enabled_types: set[str]) -> tuple[str, ...]:
    """研究算子（entity/ontology/graph_write）一律不注入，恒返回空。

    保留函数签名以兼容既有调用方；M6 随新预置体系一并清理调用点。
    """
    return ()


@dataclass(frozen=True)
class WorkflowNormalizer:
    catalog: Mapping[str, MiningOperatorDef]

    def normalize(self, graph: WorkflowGraph) -> WorkflowGraph:
        by_type = {
            node.operator_type: node
            for node in graph.nodes
            if not node.disabled and node.operator_type in _GLOBAL_CHAIN_TYPES
        }
        chain_types = ["asset_persist", "mining_finalize"]
        chain_ids = [by_type[item].node_id for item in chain_types if item in by_type]

        node_by_id = {node.node_id: node for node in graph.nodes}
        edges = [
            edge
            for edge in graph.edges
            if not (
                edge.from_node in node_by_id
                and edge.to_node in node_by_id
                and node_by_id[edge.from_node].operator_type in _GLOBAL_CHAIN_TYPES
                and node_by_id[edge.to_node].operator_type in _GLOBAL_CHAIN_TYPES
            )
        ]
        edges.extend(
            EdgeDef(source, "finalizeInput", target, "finalizeInput")
            for source, target in zip(chain_ids, chain_ids[1:])
        )
        return WorkflowGraph(
            nodes=tuple(graph.nodes),
            edges=tuple(edges),
            output=graph.output,
            schema_version=graph.schema_version,
        )
