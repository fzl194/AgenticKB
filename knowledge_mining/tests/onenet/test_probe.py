# -*- coding: utf-8 -*-
"""第一步 · 查询发现（V1.2）：三元组构造/白名单/part_id 强制精确/
原生翻页 nid 去重守卫/source_id 汇总/文档分页。"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.onenet.probe import (
    aggregate_documents, build_conditions, pull_hit_slices, search_documents,
)

# ---------------------------------------------------------------- build_conditions


def test_build_conditions_passthrough_and_dedup():
    out = build_conditions([
        {"field": "doc_name", "fuzzy": True, "content": "UDG"},
        {"field": "source_site", "fuzzy": False, "content": "support"},
        {"field": "doc_name", "fuzzy": True, "content": "   "},  # 空内容剔除
    ])
    assert out == [
        {"field": "doc_name", "fuzzy": True, "content": "UDG"},
        {"field": "source_site", "fuzzy": False, "content": "support"},
    ]


def test_build_conditions_whitelist_rejects_unknown():
    with pytest.raises(ValueError, match="invalid_field"):
        build_conditions([{"field": "hack", "fuzzy": False, "content": "x"}])


def test_build_conditions_part_id_forces_exact():
    out = build_conditions([{"field": "part_id", "fuzzy": True, "content": "5"}])
    assert out == [{"field": "part_id", "fuzzy": False, "content": "5"}]


def test_build_conditions_empty_rejected():
    with pytest.raises(ValueError, match="conditions_required"):
        build_conditions([])


# ---------------------------------------------------------------- 汇总与分页（fake client）


def _hit(nid, part_id, source_id="DOC1", doc_name="UDG 手册", title=None):
    return {"nid": nid, "part_id": part_id, "source_id": source_id,
            "doc_name": doc_name, "title": title or f"标题{part_id}",
            "parsed_version": "v1", "publish_time": "2026-07-25",
            "product_line": ["云核心网"], "file_name": f"{doc_name}.hwics",
            "doc_type": ["hwics"], "language": "cn"}


class FakeSearchClient:
    """两页命中：第 2 页含第 1 页重复 nid（单键排序翻页坑）+ 新文档。"""

    def __init__(self):
        self.calls: list[dict] = []

    def query_native(self, conditions, page_num=1, page_size=10):
        self.calls.append({"conditions": conditions, "page": page_num})
        if page_num == 1:
            rows = [_hit("n1", 1), _hit("n2", 2, source_id="DOC2", doc_name="UDM 手册")]
            return {"total": 3, "searchResults": rows}
        return {"total": 3, "searchResults": [_hit("n1", 1)]}  # n1 重复


def test_search_documents_full_flow():
    c = FakeSearchClient()
    out = search_documents(c, [
        {"field": "source_site", "fuzzy": False, "content": "support"},
    ], page=1, page_size=20)
    # 三元组透传（原生格式）
    assert c.calls[0]["conditions"] == [
        {"field": "source_site", "fuzzy": False, "content": "support"}]
    # nid 去重守卫：n1 重复被丢弃 → 2 文档
    assert out["total_documents"] == 2
    assert out["slices_pulled"] == 2
    by_sid = {d["source_id"]: d for d in out["documents"]}
    assert by_sid["DOC1"]["slice_hits"] == 1
    assert by_sid["DOC1"]["doc_name"] == "UDG 手册"
    assert by_sid["DOC2"]["sample_titles"] == ["标题2"]


def test_search_documents_pagination():
    c = FakeSearchClient()
    out = search_documents(c, [
        {"field": "doc_name", "fuzzy": True, "content": "UDG"},
    ], page=2, page_size=1)
    assert out["page"] == 2 and out["page_size"] == 1
    assert len(out["documents"]) == 1            # 第 2 页只剩第二篇
    assert out["total_documents"] == 2


def test_aggregate_documents_sample_titles_capped():
    rows = [_hit(f"n{i}", i, title=f"t{i}") for i in range(1, 6)]
    docs = aggregate_documents(rows)
    assert docs[0]["slice_hits"] == 5
    assert docs[0]["sample_titles"] == ["t1", "t2", "t3"]  # 样例封顶 3


def test_pull_hit_slices_stops_on_short_page():
    class OnePage:
        def query_native(self, cond, page_num=1, page_size=10):
            return {"total": 2, "searchResults": [_hit("n1", 1), _hit("n2", 2)]}

    slices, total = pull_hit_slices(OnePage(), [{"field": "doc_name", "fuzzy": True, "content": "x"}])
    assert len(slices) == 2 and total == 2
