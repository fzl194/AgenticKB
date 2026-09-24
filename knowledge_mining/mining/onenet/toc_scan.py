# -*- coding: utf-8 -*-
"""TOC 轻量扫描（47 号 §四-6 步骤 3）.

只拉 ``path/title/part_id`` 三字段构建章节树（15.7 万切片 ≈160 次请求），
供导入向导预览勾选。缓存由调用方（routes 层）落 ``onenet_toc_cache``。
"""
from __future__ import annotations

from typing import Any

from knowledge_mining.mining.onenet.client import OnenetClient
from knowledge_mining.mining.onenet.restore import (
    RESTORE_MODE_FILE_ANCHOR, RESTORE_MODE_PRODUCT_DOCUMENT,
    build_path_tree, recommend_restore_mode, restore_files, top_folder_segment,
)

#: 轻拉字段（实测支持 _source 投影，kone_connector fetch_source_chunk fields 参数）。
#: doc_name（V1.3）用于文件预览的顶层落位段——向导看到的目录 = 将来落库的目录。
TOC_FIELDS = [
    "path", "title", "part_id", "doc_name", "file_name", "doc_type",
]

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
    # V1.3：预览目录带顶层文档段（与 import_service._import_file 落位一致）
    doc_name = next(
        (str(s["doc_name"]) for s in slices if s.get("doc_name")), None)
    top = top_folder_segment(doc_name, source_id)
    # beta-5：同一次轻扫复用切片计算两种预览，前端切换模式无需再次扫描。
    product = restore_files(
        slices, restore_mode=RESTORE_MODE_PRODUCT_DOCUMENT)
    anchor = restore_files(slices, restore_mode=RESTORE_MODE_FILE_ANCHOR)
    recommended_mode = recommend_restore_mode(
        slices, anchor_assigned=anchor.slice_count)
    previews = {
        RESTORE_MODE_PRODUCT_DOCUMENT: _preview(product, top),
        RESTORE_MODE_FILE_ANCHOR: _preview(anchor, top),
    }
    recommended = previews[recommended_mode]
    anchor_total = len(slices)
    return {
        "source_id": source_id,
        "total_slices": total,
        "scanned_slices": len(slices),
        "scanned_part_max": hi,
        "parsed_version": parsed_version,
        "nodes": _count_nodes(root),
        "tree": [c.to_dict() for c in root.children.values()],
        "rule_version": product.rule_version,
        "recommended_restore_mode": recommended_mode,
        "anchor_coverage": {
            "assigned": anchor.slice_count,
            "total": anchor_total,
            "ratio": anchor.slice_count / anchor_total if anchor_total else 0.0,
        },
        "restore_previews": previews,
        # 兼容既有调用方：顶层字段始终投影为推荐模式的预览。
        **recommended,
    }


def _preview(result: Any, top: str) -> dict[str, Any]:
    return {
        "restore_mode": result.restore_mode,
        "file_count": len(result.files),
        "folder_count": len(result.folders),
        "slice_count": result.slice_count,
        "unassigned": result.unassigned,
        "files": [
            {"file_path": f.file_path, "file_title": f.file_title,
             "heading_title": f.heading_title,
             "folder_path": "/".join(p for p in (top, f.folder_path) if p),
             "slice_count": len(f.slices), "part_min": f.part_min,
             "part_max": f.part_max}
            for f in result.files
        ],
    }


def _count_nodes(node: Any) -> int:
    n = 1 if node.depth > 0 else 0
    for child in node.children.values():
        n += _count_nodes(child)
    return n


__all__ = ["EMPTY_SOURCE_ERROR", "TOC_FIELDS", "scan_toc"]
