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
    #: 36号根因 6：同 (table_ref, row, column_index) 重复 cell 防御性
    #: 丢弃计数（保留首个）——形状异常的表不得炸掉整篇文档的 asset_persist。
    dropped_duplicate_cells: int = 0


def _section_ref(document_ref: str, path: Sequence[tuple[int, str]]) -> str:
    return f"{document_ref}#section:{'/'.join(title for _lvl, title in path)}"


def _row_cells(raw_text: str, header: Sequence[str]) -> list[tuple[str, str]]:
    """解析自描述行文本（compiler ``_row_text``："列名=值；列名=值"）.

    27号审查修复（E2E 追溯发现）：table_row 的 raw_text 是"列名=值"自描述
    格式而非 \\t 分隔——按表头名对齐恢复逐列 cell；无表头对应的片段
    （如 "[caption] " 前缀、表头未覆盖的裸值）跳过。值内含 "；" 属罕见
    边界，接受尽力恢复语义。
    """
    header_set = set(header)
    out: list[tuple[str, str]] = []
    for part in raw_text.split("；"):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        if sep and name.strip() in header_set:
            out.append((name.strip(), value))
    return out


def project_structure(
    segments: Iterable[CompiledSegment],
    *,
    document_ref: str,
) -> StructureProjection:
    materialized = tuple(segments)
    # A0-2：document 节点 ref 与 retrieval 的 document target_ref 同身份
    # （{doc}#document）——此前裸 {doc} 让 st_（按 target_ref 编码）可解码但
    # inspect/children 按节点表精确匹配不到。所有指向文档节点的 parent 同步用该 ref。
    doc_node_ref = f"{document_ref}#document"
    nodes: list[dict[str, Any]] = [
        {"node_type": "document", "ref": doc_node_ref, "title": document_ref}
    ]
    edges: list[dict[str, Any]] = []
    seen_sections: dict[tuple[tuple[int, str], ...], str] = {}

    for segment in materialized:
        parent_ref = doc_node_ref
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
    # 29号 R02：真实数据行数按 table_ref 去重计数（此前 max(row_index)+1
    # 在行号稀疏/跳跃时虚报）。
    data_rows_by_table: dict[str, set[int]] = {}
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
                if segment.heading_chain else doc_node_ref,
            })
        if segment.block_type == "table_row" and header:
            row_index = int(metadata.get("row_index", len(cells)))
            col_idx_of = {name: i for i, name in enumerate(header)}
            # 29号 R02：优先消费编译器传播的精确 cell 事实（row_cells）；
            # 自描述文本解析仅作 legacy 行兜底。
            # 36号根因 6：三元组 [name, value, column_index] 携带真实列号
            # （表头外列不再塌缩 -1）；29号二元组（已落库行）按列名反查
            # 兜底，保持读取端兼容。
            raw_pairs = metadata.get("row_cells")
            if raw_pairs:
                pairs = []
                for pair in raw_pairs:
                    if not isinstance(pair, (list, tuple)) or len(pair) not in (2, 3):
                        continue
                    name, value = str(pair[0]), str(pair[1])
                    idx = None
                    if len(pair) == 3:
                        # 恶意/异常形状的第三元素不得炸整篇 persist
                        # （36号审查 LOW-6：与防御去重同一防线）。
                        try:
                            idx = int(pair[2])
                        except (TypeError, ValueError):
                            idx = None
                    pairs.append((name, value, idx))
            else:
                pairs = [
                    (name, value, None)
                    for name, value in _row_cells(segment.raw_text, header)
                ]
            for name, value, true_index in pairs:
                if not value.strip():
                    continue
                if true_index is not None and true_index >= 0:
                    column_index = true_index
                else:
                    column_index = col_idx_of.get(name, -1)
                cells.append({
                    "table_ref": table_ref, "row": row_index,
                    "column_index": column_index,
                    "column": name, "value": value.strip(),
                    "is_header": False,
                })
            data_rows_by_table.setdefault(table_ref, set()).add(row_index)

    for asset in table_assets:
        asset["row_count"] = len(data_rows_by_table.get(asset["table_ref"], ()))

    # 36号根因 6：防御性去重——staging 主键 (snapshot_id, table_ref,
    # row_index, column_index) 上重复（legacy 兜底 -1、异常形状）保留
    # 首个并计数，一张表的形状问题不得炸掉整篇文档入库。
    unique_cells: list[dict[str, Any]] = []
    seen_cell_keys: set[tuple[str, int, int]] = set()
    dropped = 0
    for cell in cells:
        key = (cell["table_ref"], cell["row"], cell["column_index"])
        if key in seen_cell_keys:
            dropped += 1
            continue
        seen_cell_keys.add(key)
        unique_cells.append(cell)

    return StructureProjection(
        nodes=tuple(nodes),
        edges=tuple(edges),
        table_assets=tuple(table_assets),
        table_cells=tuple(unique_cells),
        dropped_duplicate_cells=dropped,
    )


__all__ = ["StructureProjection", "project_structure"]
