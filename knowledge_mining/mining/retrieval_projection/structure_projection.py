"""结构化面投影（批次8 M5，24 号 §3.1/§5.8）.

CompiledSegment → structure nodes/edges（确定性 parent/order）+ typed
table assets + table cells。不生成 LLM 关系；caption/footnote 等
explicit reference 边由 metadata 存在时才产出。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Mapping

from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment

_TABLE_TYPES = {"table", "table_row"}


@dataclass(frozen=True)
class StructureProjection:
    nodes: tuple[dict[str, Any], ...] = ()
    edges: tuple[dict[str, Any], ...] = ()
    table_assets: tuple[dict[str, Any], ...] = ()
    table_cells: tuple[dict[str, Any], ...] = ()


def _section_ref(document_ref: str, path: Sequence[tuple[int, str]]) -> str:
    return f"{document_ref}#section:{'/'.join(title for _lvl, title in path)}"


def project_structure(
    segments: Iterable[CompiledSegment],
    *,
    document_ref: str,
) -> StructureProjection:
    materialized = tuple(segments)
    nodes: list[dict[str, Any]] = [
        {"node_type": "document", "ref": document_ref, "title": document_ref}
    ]
    edges: list[dict[str, Any]] = []
    seen_sections: dict[tuple[tuple[int, str], ...], str] = {}

    for segment in materialized:
        parent_ref = document_ref
        chain = tuple(segment.heading_chain)
        for depth in range(1, len(chain) + 1):
            path = chain[:depth]
            ref = seen_sections.get(path)
            if ref is None:
                level, title = path[-1]
                ref = _section_ref(document_ref, path)
                seen_sections[path] = ref
                nodes.append({
                    "node_type": "section", "ref": ref, "title": title,
                    "level": level, "parent_ref": parent_ref,
                })
                edges.append({
                    "relation": "parent", "from_ref": ref, "to_ref": parent_ref,
                })
            parent_ref = ref

        seg_ref = f"{document_ref}#seg:{segment.segment_index}"
        nodes.append({
            "node_type": "segment", "ref": seg_ref,
            "parent_ref": parent_ref, "ordinal": segment.segment_index,
            "block_type": segment.block_type,
        })
        edges.append({"relation": "parent", "from_ref": seg_ref, "to_ref": parent_ref})
        if segment.segment_index > 0:
            edges.append({
                "relation": "order", "from_ref": seg_ref,
                "to_ref": f"{document_ref}#seg:{segment.segment_index - 1}",
            })

    table_assets: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    seen_tables: set[str] = set()
    for segment in materialized:
        if segment.block_type not in _TABLE_TYPES:
            continue
        metadata: Mapping[str, Any] = segment.metadata or {}
        table_ref = str(metadata.get("table_ref") or f"tbl:{segment.segment_index}")
        header = [str(col) for col in (metadata.get("table_header") or ())]
        if table_ref not in seen_tables:
            seen_tables.add(table_ref)
            table_assets.append({
                "asset_type": "table",
                "asset_ref": f"{document_ref}#table:{table_ref}",
                "table_ref": table_ref,
                "columns": header,
                "row_count": 0,
                "readiness": "ready" if header else "insufficient",
            })
            nodes.append({
                "node_type": "table", "ref": f"{document_ref}#table:{table_ref}",
                "parent_ref": _section_ref(document_ref, tuple(segment.heading_chain))
                if segment.heading_chain else document_ref,
            })
        if segment.block_type == "table_row" and header:
            values = segment.raw_text.split("\t")
            row_index = int(metadata.get("row_index", len(cells)))
            for col_idx, (column, value) in enumerate(zip(header, values)):
                if not value.strip():
                    continue
                cells.append({
                    "table_ref": table_ref, "row": row_index, "column_index": col_idx,
                    "column": column, "value": value.strip(),
                    "is_header": False,
                })
            for asset in table_assets:
                if asset["table_ref"] == table_ref:
                    asset["row_count"] = max(asset["row_count"], row_index + 1)

    return StructureProjection(
        nodes=tuple(nodes),
        edges=tuple(edges),
        table_assets=tuple(table_assets),
        table_cells=tuple(cells),
    )


__all__ = ["StructureProjection", "project_structure"]
