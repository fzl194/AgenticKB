# -*- coding: utf-8 -*-
"""TOC 轻量扫描（47 号 §四-6 步骤 3）.

只拉 ``path/title/part_id`` 三字段构建章节树（15.7 万切片 ≈160 次请求），
供导入向导预览勾选。缓存由调用方（routes 层）落 ``onenet_toc_cache``。
"""
from __future__ import annotations

from typing import Any

from knowledge_mining.mining.onenet.client import OnenetClient
from knowledge_mining.mining.onenet.restore import build_path_tree

#: 轻拉字段（实测支持 _source 投影，kone_connector fetch_source_chunk fields 参数）。
TOC_FIELDS = ["path", "title", "part_id"]

EMPTY_SOURCE_ERROR = "source 无切片"


def scan_toc(
    client: OnenetClient, source_id: str, *,
    max_part_id: int | None = None,
    chunk_width: int = 10000,
    page_size: int = 1000,
) -> dict[str, Any]:
    """扫描 source 章节树：{source_id, total_slices, parsed_version, nodes, tree}.

    ``max_part_id`` 子集模式（联调抽查用）；空 source 抛 ValueError。
    树节点形状见 restore.TreeNode.to_dict（title/path/depth/slice_count/
    part_min/part_max/children）。
    """
    total = client.count_source(source_id)
    if total <= 0:
        raise ValueError(f"{EMPTY_SOURCE_ERROR}: {source_id}")
    pr = client.part_range(source_id)
    hi = min(pr.get("max") or total, max_part_id) if max_part_id else (pr.get("max") or total)
    lo = pr.get("min") or 1

    slices: list[dict[str, Any]] = []
    seg_lo = int(lo)
    while seg_lo <= hi:
        seg_hi = min(seg_lo + chunk_width - 1, hi)
        slices.extend(client.fetch_source_chunk(
            source_id, seg_lo, seg_hi, page_size=page_size, fields=list(TOC_FIELDS)))
        seg_lo = seg_hi + 1

    parsed_version: str | None = None
    for s in slices:
        if s.get("parsed_version"):
            parsed_version = str(s["parsed_version"])
            break

    root = build_path_tree(slices)
    return {
        "source_id": source_id,
        "total_slices": total,
        "scanned_slices": len(slices),
        "scanned_part_max": hi,
        "parsed_version": parsed_version,
        "nodes": sum(1 for _ in root.iter_slices()) and _count_nodes(root),
        "tree": [c.to_dict() for c in root.children.values()],
    }


def _count_nodes(node: Any) -> int:
    n = 1 if node.depth > 0 else 0
    for child in node.children.values():
        n += _count_nodes(child)
    return n


__all__ = ["EMPTY_SOURCE_ERROR", "TOC_FIELDS", "scan_toc"]
