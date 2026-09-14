# -*- coding: utf-8 -*-
"""批次拉取器：段幂等 / 子树过滤 / 完整性校验 / 段选择去重 / manifest."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from knowledge_mining.mining.onenet.client import OnenetClient
from knowledge_mining.mining.onenet.fetch import (
    FetchVerifyError, Selection, fetch_selection, load_slices,
)

TOKEN_URL = "http://oauth2.test/token"
SEARCH_URL = "http://apigw.test/search?source_type=0"
PKG = "Pkg.hwics"


def _row(part_id: int, path: str) -> dict:
    return {"nid": f"n{part_id}", "part_id": part_id, "path": path,
            "title": path.split(" > ")[-1], "content": f"c{part_id}",
            "source_id": "DOC1"}


class FakeFetch:
    """全量行可编程 + range 过滤的搜索端点."""

    def __init__(self, rows: list[dict]):
        self.rows = sorted(rows, key=lambda r: r["part_id"])
        self.range_requests = 0
        self.asleep: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url == TOKEN_URL:
            return httpx.Response(200, json={"result": "Basic t"})
        body = json.loads(request.content.decode("utf-8"))
        dsl = json.loads(body["dsl"])
        if "track_total_hits" in dsl and (dsl.get("size") or 0) <= 1:
            return httpx.Response(200, json={"total": len(self.rows), "searchResults": []})
        sort = json.dumps(dsl.get("sort") or {})
        if "sort" in dsl and (dsl.get("size") or 0) <= 1:
            if "desc" in sort:
                return httpx.Response(200, json={"total": 1, "searchResults": [
                    {"part_id": self.rows[-1]["part_id"]}]})
            return httpx.Response(200, json={"total": 1, "searchResults": [
                {"part_id": self.rows[0]["part_id"]}]})
        self.range_requests += 1
        rng = dsl["query"]["bool"]["must"][1]["range"]["part_id"]
        rows = [r for r in self.rows if rng["gte"] <= r["part_id"] <= rng["lte"]]
        frm = dsl["from"]
        page = rows[frm:frm + dsl["size"]]
        return httpx.Response(200, json={"total": len(rows), "searchResults": page})


def _client(fake: FakeFetch, throttle: float = 0.0) -> OnenetClient:
    return OnenetClient(app_id="a", static_token="s", token_url=TOKEN_URL,
                        search_url=SEARCH_URL,
                        transport=httpx.MockTransport(fake.handler))


ROWS = (
    [_row(1, f"{PKG} > A > B"), _row(2, f"{PKG} > A > C"),
     _row(3, f"{PKG} > D > E"), _row(4, f"{PKG} > D > F"),
     _row(5, f"{PKG} > G")]
)


# ------------------------------------------------------------- Selection


def test_selection_prefix_match_by_segments():
    sel = Selection(subtrees=("A",))
    assert sel.matches(_row(1, f"{PKG} > A > B"))
    assert sel.matches(_row(9, f"{PKG} > A > X > Y"))       # 深层后代
    assert not sel.matches(_row(3, f"{PKG} > AB > E"))      # 段前缀，非字符串前缀
    assert not sel.matches(_row(3, f"{PKG} > D > E"))
    assert Selection().matches(_row(1, f"{PKG} > A > B"))   # 空 = 整包


def test_selection_roundtrip():
    sel = Selection(subtrees=("A", "D > E"), max_part_id=2000)
    data = sel.to_dict()
    back = Selection.from_dict(data)
    assert back == sel
    assert Selection.from_dict(None) == Selection()
    assert Selection.from_dict({"subtrees": ["", "  "], "max_part_id": None}) == Selection()


# ------------------------------------------------------------- fetch_selection


def test_fetch_full_writes_segments_and_manifest(tmp_path):
    fake = FakeFetch(list(ROWS))
    out = fetch_selection(_client(fake), "DOC1", Selection(),
                          tmp_path, chunk_width=3, throttle_seconds=0)
    assert out.slice_count == 5
    seg_files = sorted(p.name for p in (tmp_path / "parts").iterdir())
    assert seg_files == ["part_1_3.jsonl", "part_4_5.jsonl"]
    assert out.manifest["doc_total_slices"] == 5
    assert out.manifest["fetch_mode"] == "full"
    assert out.manifest["verify"]["ok"] is True
    assert (tmp_path / "manifest.json").exists()
    assert len(load_slices(out.slices_path)) == 5


def test_fetch_segment_idempotent_skip(tmp_path):
    fake = FakeFetch(list(ROWS))
    fetch_selection(_client(fake), "DOC1", Selection(),
                    tmp_path, chunk_width=3, throttle_seconds=0)
    n1 = fake.range_requests
    fetch_selection(_client(fake), "DOC1", Selection(),
                    tmp_path, chunk_width=3, throttle_seconds=0)
    assert fake.range_requests == n1  # 段全跳过，无新拉取


def test_fetch_subtree_filter(tmp_path):
    fake = FakeFetch(list(ROWS))
    out = fetch_selection(_client(fake), "DOC1", Selection(subtrees=("A",)),
                          tmp_path, chunk_width=10, throttle_seconds=0)
    assert out.slice_count == 2
    paths = {r["path"] for r in load_slices(out.slices_path)}
    assert paths == {f"{PKG} > A > B", f"{PKG} > A > C"}


def test_fetch_max_part_id_subset(tmp_path):
    fake = FakeFetch(list(ROWS))
    out = fetch_selection(_client(fake), "DOC1", Selection(max_part_id=2),
                          tmp_path, chunk_width=10, throttle_seconds=0)
    assert out.slice_count == 2
    assert out.manifest["fetched_max_part_id"] == 2


def test_fetch_verify_detects_dup(tmp_path):
    # 预置一个含重复 nid 的段 → 校验抛错
    fake = FakeFetch(list(ROWS))
    fetch_selection(_client(fake), "DOC1", Selection(),
                    tmp_path, chunk_width=10, throttle_seconds=0)
    data = tmp_path / "slices.jsonl"
    lines = data.read_text(encoding="utf-8").splitlines()
    data.write_text("\n".join(lines + [lines[0]]) + "\n", encoding="utf-8")
    with pytest.raises(FetchVerifyError, match="dup_nid"):
        from knowledge_mining.mining.onenet.fetch import verify_file
        verify_file(data)


def test_fetch_empty_source_raises(tmp_path):
    fake = FakeFetch([])
    with pytest.raises(FetchVerifyError, match="无切片"):
        fetch_selection(_client(fake), "DOC_NONE", Selection(), tmp_path)


def test_select_segments_partial_overlap_rejected(tmp_path):
    # 部分重叠且互不包含 → 拒绝（直接单测段选择，避开 fetch 循环建新段的干扰）
    from knowledge_mining.mining.onenet.fetch import _select_segments
    parts = tmp_path / "parts"
    parts.mkdir(parents=True)
    (parts / "part_1_3.jsonl").write_text("", encoding="utf-8")
    (parts / "part_2_5.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(FetchVerifyError, match="重叠"):
        _select_segments(parts)


def test_select_segments_contained_dedup_keeps_wider(tmp_path):
    from knowledge_mining.mining.onenet.fetch import _select_segments
    parts = tmp_path / "parts"
    parts.mkdir(parents=True)
    (parts / "part_1_2000.jsonl").write_text("", encoding="utf-8")
    (parts / "part_1_3000.jsonl").write_text("", encoding="utf-8")
    (parts / "part_3001_4000.jsonl").write_text("", encoding="utf-8")
    kept = [p.name for p in _select_segments(parts)]
    assert kept == ["part_1_3000.jsonl", "part_3001_4000.jsonl"]
