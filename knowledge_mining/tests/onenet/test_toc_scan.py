# -*- coding: utf-8 -*-
"""TOC 轻量扫描（三字段投影 + 树聚合 + 子集模式 + 空 source）."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from knowledge_mining.mining.onenet.client import OnenetClient
from knowledge_mining.mining.onenet.toc_scan import TOC_FIELDS, scan_toc

TOKEN_URL = "http://oauth2.test/token"
SEARCH_URL = "http://apigw.test/search?source_type=0"
FIXTURE = Path(__file__).parent / "fixtures" / "udg_sample_32.json"


def _sample_rows() -> list[dict]:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [{"path": r["path"], "title": r["title"],
             "part_id": r["part_id"], "doc_name": r.get("doc_name")}
            for r in rows]


class FakeScan:
    """预置全量行的分页搜索端点."""

    def __init__(self, rows: list[dict], total: int, part_max: int):
        self.rows = rows
        self.total = total
        self.part_max = part_max
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url == TOKEN_URL:
            return httpx.Response(200, json={"result": "Basic t"})
        body = json.loads(request.content.decode("utf-8"))
        self.requests.append(body)
        dsl = json.loads(body["dsl"]) if "dsl" in body else {}
        sort = json.dumps(dsl.get("sort") or {})
        if "sort" in dsl and (dsl.get("size") or 0) <= 1:
            # part_range 探测（asc 取真实最小 / desc 取 part_max）
            if "desc" in sort:
                row = {"part_id": self.part_max}
            else:
                part_min = min((r["part_id"] for r in self.rows), default=1)
                row = {"part_id": part_min}
            return httpx.Response(200, json={"total": 1, "searchResults": [row]})
        if "track_total_hits" in dsl and (dsl.get("size") or 0) <= 1:
            return httpx.Response(200, json={"total": self.total, "searchResults": []})
        rng = dsl["query"]["bool"]["must"][1]["range"]["part_id"]
        rows = [r for r in self.rows if rng["gte"] <= r["part_id"] <= rng["lte"]]
        frm = dsl["from"]
        return httpx.Response(200, json={
            "total": len(rows),
            "searchResults": rows[frm:frm + dsl["size"]],
        })


def _client(fake: FakeScan) -> OnenetClient:
    return OnenetClient(app_id="a", static_token="s", token_url=TOKEN_URL,
                        search_url=SEARCH_URL, transport=httpx.MockTransport(fake.handler))


def test_scan_builds_tree_with_counts_and_projection():
    rows = _sample_rows()
    fake = FakeScan(rows, total=32, part_max=12325)
    out = scan_toc(_client(fake), "DOC1")
    assert out["total_slices"] == 32
    assert out["scanned_slices"] == 32
    assert out["nodes"] == 42  # 32 切片去重后 path 节点数 + 包段（beta-2 不剔）
    # 三字段投影
    for body in fake.requests:
        dsl = json.loads(body["dsl"])
        if "sort" in dsl and dsl.get("_source"):
            assert dsl["_source"] == TOC_FIELDS
    # 树：顶层 = 包段（beta-2 原样保留），聚合全部切片
    top = out["tree"][0]
    assert top["title"] == "UDG 20.18.0 产品文档 01（虚机容器）.hwics"
    assert top["slice_count"] == 32


def test_scan_subset_max_part_id():
    rows = [r for r in _sample_rows() if r["part_id"] <= 12295]
    fake = FakeScan(rows, total=32, part_max=12325)
    out = scan_toc(_client(fake), "DOC1", max_part_id=12295)
    assert out["scanned_slices"] == 2
    assert out["scanned_part_max"] == 12295
    assert out["total_slices"] == 32  # 摸底仍是全量


def test_scan_files_preview_prefixed_with_top_doc_segment():
    """V1.3：文件预览 folder_path 带顶层文档段——向导看到的目录=将来落库的目录."""
    rows = _sample_rows()
    fake = FakeScan(rows, total=32, part_max=12325)
    out = scan_toc(_client(fake), "DOC1101733708")
    top = "UDG 20.18.0 产品文档 01（虚机容器） [DOC1101733708]"
    assert out["files"] and all(
        f["folder_path"] == top or f["folder_path"].startswith(top + "/")
        for f in out["files"])
    # 树路径不带头顶段（selection 匹配键 = 剔包名上游段）
    assert all(not n["path"].startswith(top) for n in out["tree"])


def test_toc_files_preview_reflects_calibration():
    """跨段标题在预览 files/tree 中已合并（预览=落库同源，53 号 §五-1）."""
    rows = [
        {"path": "包 > 接口管理 > 告警 > 处理建议", "title": "告警 > 处理建议",
         "part_id": 1},
        {"path": "包 > 接口管理 > 定位思路", "title": "定位思路", "part_id": 2},
    ]
    toc = scan_toc(_client(FakeScan(rows, total=2, part_max=2)), "SRC1")
    assert toc["rule_version"] == "beta-5"
    assert toc["file_count"] == 1
    assert toc["files"][0]["file_path"] == "包 > 接口管理"
    assert toc["files"][0]["heading_title"] == "告警 > 处理建议"
    # 树上无假层级「告警」节点
    assert "告警" not in [c["title"] for c in toc["tree"][0]["children"][0]["children"]]


def test_toc_exposes_both_restore_previews_and_recommends_file_anchor():
    rows = [
        {"path": "资料 > 手册.pdf > 第一章", "title": "第一章", "part_id": 1},
        {"path": "资料 > 手册.pdf > 第二章", "title": "第二章", "part_id": 2},
    ]
    toc = scan_toc(_client(FakeScan(rows, total=2, part_max=2)), "SRC1")
    assert toc["recommended_restore_mode"] == "file_anchor"
    assert toc["restore_previews"]["file_anchor"]["file_count"] == 1
    assert toc["restore_previews"]["file_anchor"]["unassigned"] == 0
    assert toc["restore_previews"]["product_document"]["file_count"] == 1


def test_scan_empty_source_raises():
    fake = FakeScan([], total=0, part_max=0)
    with pytest.raises(ValueError, match="source 无切片"):
        scan_toc(_client(fake), "DOC_NONE")
