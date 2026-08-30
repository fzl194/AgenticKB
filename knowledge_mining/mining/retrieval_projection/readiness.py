"""readiness 四能力事实（批次8 M5，24 号 §7）.

能力由真实资产推导，不能由「图里有算子」或 handler SUCCESS 推断：
- search_ready：有可解析 canonical source 的 lexical 表示；
- structure_navigate_ready：结构 nodes/parent/order 完整；
- structured_query_ready：≥1 typed asset 通过 schema/质量门；
- aggregate_ready：目标字段具稳定 value type 且允许对应聚合。
另记 dense_ready（混合预置判定用）与增强事实位（M3 填充）。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrieRepresentation,
)
from knowledge_mining.mining.retrieval_projection.structure_projection import (
    StructureProjection,
)


def column_aggregability(
    cells: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """按列判定可聚合性：全数值列 → count/sum/min/max/avg 允许."""
    by_column: dict[str, list[str]] = {}
    for cell in cells:
        by_column.setdefault(str(cell.get("column")), []).append(
            str(cell.get("value", ""))
        )
    result: dict[str, dict[str, Any]] = {}
    for column, values in by_column.items():
        numeric = all(_is_number(value) for value in values if values)
        result[column] = {
            "value_type": "number" if numeric else "text",
            "can_aggregate": numeric,
            "operations": ["count", "sum", "min", "max", "avg"] if numeric else ["count"],
            "sample_size": len(values),
        }
    return result


def _is_number(value: str) -> bool:
    try:
        float(value.replace(",", ""))
        return True
    except ValueError:
        return False


def compute_readiness(
    *,
    representations: Sequence[RetrievalRepresentation],
    structure: StructureProjection | Mapping[str, Any],
    embedding_records: Sequence[Any] = (),
) -> dict[str, Any]:
    lexical_units = [
        rep for rep in representations if rep.lexical_eligible and rep.target_ref
    ]
    dense_units = [rep for rep in representations if rep.dense_eligible]

    nodes = getattr(structure, "nodes", None) or (
        structure.get("nodes", ()) if isinstance(structure, Mapping) else ()
    )
    edges = getattr(structure, "edges", None) or (
        structure.get("edges", ()) if isinstance(structure, Mapping) else ()
    )
    table_assets = getattr(structure, "table_assets", None) or (
        structure.get("table_assets", ()) if isinstance(structure, Mapping) else ()
    )
    table_cells = getattr(structure, "table_cells", None) or (
        structure.get("table_cells", ()) if isinstance(structure, Mapping) else ()
    )

    ready_tables = [
        asset for asset in table_assets
        if asset.get("readiness") == "ready" and asset.get("columns")
    ]
    aggregability = column_aggregability(table_cells)

    has_parent = any(
        edge.get("relation") == "parent" for edge in edges
    )
    has_order = any(edge.get("relation") == "order" for edge in edges)
    # 27号审查修复：单段文档没有 order 边（segment_index>0 才产出）——
    # 有 parent 边且 segment 节点 ≤1 时导航仍判定可用。
    segment_node_count = sum(
        1 for node in nodes
        if (node.get("node_type") if isinstance(node, Mapping) else None)
        == "segment"
    )

    # 27号审查修复：dimension≤0 的记录（历史空向量/占位）不计入覆盖——
    # dense_ready 不因存在无向量行而虚报。
    embedded_ids = {
        getattr(record, "representation_id", None)
        for record in embedding_records
        if int(getattr(record, "dimension", 0) or 0) > 0
    }
    dense_covered = sum(
        1 for rep in dense_units if rep.representation_id in embedded_ids
    )

    return {
        "search_ready": bool(lexical_units),
        "dense_ready": bool(dense_units) and dense_covered > 0,
        "structure_navigate_ready": bool(nodes) and has_parent and (
            has_order or segment_node_count <= 1
        ),
        # 27号审查修复：结构化查询需真实数据行——只有表头没有 cell 的
        # "ready" 表不足以支撑 query_structured_asset（工具已建、数据面空）。
        "structured_query_ready": bool(ready_tables) and bool(table_cells),
        "aggregate_ready": any(col["can_aggregate"] for col in aggregability.values()),
        "column_aggregability": aggregability,
        "counts": {
            "lexical_units": len(lexical_units),
            "dense_units": len(dense_units),
            "dense_covered": dense_covered,
            "structure_nodes": len(nodes),
            "structure_edges": len(edges),
            "table_assets": len(ready_tables),
            "table_cells": len(table_cells),
        },
    }


__all__ = ["column_aggregability", "compute_readiness"]
