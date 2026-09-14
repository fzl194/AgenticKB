# -*- coding: utf-8 -*-
"""一张网摸底（47 号 §四-6 / §十一 V1.2 原型验证修订）.

第一步 · 查询发现：
- 三元组条件透传（12 字段白名单 × 精确/模糊 × 内容，AND）；
- 原生 searchQueryList 翻页拉满 from+size≤10000 + nid 去重守卫
  （单键 sortField 翻页重复兜底——原型内网实证）；
- 按 source_id 聚合为文档行（文档级字段取首条命中切片）+ 命中章节样例；
- 文档列表分页；命中 ≥10000 封顶如实标注。
"""
from __future__ import annotations

from typing import Any

from knowledge_mining.mining.onenet.client import OnenetClient

#: 查询字段白名单（47 号 §十一：用户定义 12 字段，顺序即前端下拉顺序）。
SEARCH_FIELDS = (
    "source_id", "nid", "url", "title", "path", "content",
    "source_site", "file_name", "category_path", "doc_name",
    "doc_type", "part_id",
)

#: 数字字段：模糊（分词）无意义，强制精确。
NUMERIC_FIELDS = frozenset({"part_id"})

#: 命中封顶阈值（from+size ≤ 10000 接口硬上限）。
_MAX_SLICES = 10000
_PAGE_SIZE = 1000


def build_conditions(raw_conditions: list[dict[str, Any]]) -> list[dict]:
    """三元组校验与规整：白名单字段、part_id 强制精确、剔空内容。"""
    out: list[dict] = []
    for c in raw_conditions or []:
        field = str(c.get("field") or "").strip()
        content = str(c.get("content") or "").strip()
        if field not in SEARCH_FIELDS:
            raise ValueError(
                f"invalid_field: {field}（允许: {', '.join(SEARCH_FIELDS)}）")
        if not content:
            continue
        fuzzy = bool(c.get("fuzzy")) and field not in NUMERIC_FIELDS
        out.append({"field": field, "fuzzy": fuzzy, "content": content})
    if not out:
        raise ValueError("conditions_required: 至少一条有效查询条件")
    return out


def pull_hit_slices(
    client: OnenetClient, conditions: list[dict], max_slices: int = _MAX_SLICES,
) -> tuple[list[dict], int | None]:
    """原生翻页拉取命中切片 + nid 去重守卫（对齐原型 pull_slices）。"""
    slices: list[dict] = []
    seen_nid: set[str] = set()
    total: int | None = None
    page = 1
    while ((page - 1) * _PAGE_SIZE + _PAGE_SIZE <= _MAX_SLICES
           and len(slices) < max_slices):
        res = client.query_native(conditions, page_num=page, page_size=_PAGE_SIZE)
        if total is None:
            total = int(res.get("total") or 0)
        rows = res.get("searchResults") or []
        if not rows:
            break
        for row in rows:
            nid = str(row.get("nid") or "")
            if nid and nid in seen_nid:
                continue  # 单键排序翻页重复守卫
            if nid:
                seen_nid.add(nid)
            slices.append(row)
        if len(rows) < _PAGE_SIZE:
            break
        page += 1
    return slices[:max_slices], total


def aggregate_documents(slices: list[dict]) -> list[dict[str, Any]]:
    """按 source_id 聚合 → 文档行（保持命中顺序=首现顺序）。"""
    docs: dict[str, dict[str, Any]] = {}
    for row in slices:
        sid = str(row.get("source_id") or "").strip()
        if not sid:
            continue
        doc = docs.get(sid)
        if doc is None:
            doc = {
                "source_id": sid,
                "doc_name": row.get("doc_name"),
                "file_name": row.get("file_name"),
                "doc_type": row.get("doc_type"),
                "parsed_version": row.get("parsed_version"),
                "publish_time": row.get("publish_time"),
                "product_line": row.get("product_line"),
                "language": row.get("language"),
                "slice_hits": 0,
                "sample_titles": [],
            }
            docs[sid] = doc
        doc["slice_hits"] += 1
        title = row.get("title")
        if title and len(doc["sample_titles"]) < 3 and title not in doc["sample_titles"]:
            doc["sample_titles"].append(title)
    return list(docs.values())


def search_documents(
    client: OnenetClient, conditions: list[dict[str, Any]], *,
    page: int = 1, page_size: int = 20,
) -> dict[str, Any]:
    """第一步 · 查询发现（V1.2）：三元组透传 → 拉满 → 汇总 → 文档分页。"""
    normalized = build_conditions(conditions)
    page = max(int(page), 1)
    page_size = min(max(int(page_size), 1), 100)
    slices, total = pull_hit_slices(client, normalized)
    documents = aggregate_documents(slices)
    start = (page - 1) * page_size
    return {
        "documents": documents[start:start + page_size],
        "total_documents": len(documents),
        "page": page,
        "page_size": page_size,
        "slice_total_reported": total,
        "capped": (total or 0) >= _MAX_SLICES,
        "slices_pulled": len(slices),
    }


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


__all__ = [
    "NUMERIC_FIELDS", "SEARCH_FIELDS", "aggregate_documents",
    "build_conditions", "probe_source", "pull_hit_slices", "search_documents",
]
