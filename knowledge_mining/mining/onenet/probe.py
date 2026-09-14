# -*- coding: utf-8 -*-
"""一张网摸底（47 号 §四-6 步骤 1/2）：多字段查询 → source_id 去重 / 单源摸底卡片.

管理面「查询/摸底」的纯组合层（不触 DB）。fuzzy 查询只作发现手段：
单查询命中封顶 10000，结果可能不全（capped 标志如实标注）。
"""
from __future__ import annotations

from typing import Any

from knowledge_mining.mining.onenet.client import OnenetClient

#: 支持的查询字段 → fuzzy 语义（实测 01_api_protocol §2.1）。
#: 名称类走模糊发现；类型/语言走精确过滤。
_SEARCH_FIELDS: dict[str, bool] = {
    "doc_name": True,
    "file_name": True,
    "doc_type": False,
    "language": False,
}

#: 命中封顶阈值（total ≥ 该值时 capped=True）。
_CAP_THRESHOLD = 10000


def search_documents(
    client: OnenetClient, filters: dict[str, str | None],
    *, page_size: int = 1000,
) -> list[dict[str, Any]]:
    """按过滤条件查询文档级结果（切片按 source_id 去重）.

    返回行：source_id / doc_name / file_name / doc_type / parsed_version /
    publish_time / product_line / pbi / slice_hits / capped。
    """
    conditions = [
        {"field": field, "fuzzy": fuzzy, "content": str(filters[field]).strip()}
        for field, fuzzy in _SEARCH_FIELDS.items()
        if filters.get(field)
    ]
    if not conditions:
        raise ValueError("filters must include at least one of: "
                         + ", ".join(_SEARCH_FIELDS))
    res = client.query_native(conditions, page_num=1, page_size=page_size)
    total = int(res.get("total") or 0)
    capped = total >= _CAP_THRESHOLD
    by_source: dict[str, dict[str, Any]] = {}
    for row in res.get("searchResults") or []:
        sid = str(row.get("source_id") or "").strip()
        if not sid:
            continue
        entry = by_source.get(sid)
        if entry is None:
            entry = {
                "source_id": sid,
                "doc_name": row.get("doc_name"),
                "file_name": row.get("file_name"),
                "doc_type": row.get("doc_type"),
                "parsed_version": row.get("parsed_version"),
                "publish_time": row.get("publish_time"),
                "product_line": row.get("product_line"),
                "pbi": row.get("pbi"),
                "slice_hits": 0,
                "capped": capped,
            }
            by_source[sid] = entry
        entry["slice_hits"] += 1
    return list(by_source.values())


def probe_source(client: OnenetClient, source_id: str) -> dict[str, Any]:
    """单源摸底卡片（count + part 范围 + 元数据；不带样例正文）."""
    profile = client.probe_source(source_id)
    return {
        "source_id": profile["source_id"],
        "total_slices": profile["total_slices"],
        "part_id": profile["part_id"],
        "doc_name": profile["doc_name"],
        "file_name": profile["file_name"],
        "doc_type": profile["doc_type"],
        "parsed_version": profile["parsed_version"],
        "publish_time": profile["publish_time"],
        "product_line": profile["product_line"],
        "pbi": profile["pbi"],
    }


__all__ = ["probe_source", "search_documents"]
