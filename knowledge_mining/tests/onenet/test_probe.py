# -*- coding: utf-8 -*-
"""probe 摸底（多字段查询 → source_id 去重 → 文档行 / 单源摸底卡片）."""
from __future__ import annotations

import json

import httpx
import pytest

from knowledge_mining.mining.onenet.client import OnenetClient
from knowledge_mining.mining.onenet.probe import probe_source, search_documents

TOKEN_URL = "http://oauth2.test/token"
SEARCH_URL = "http://apigw.test/search?source_type=0"


def _doc_slice(source_id: str, part_id: int, doc_name: str) -> dict:
    return {
        "nid": f"hwics_{source_id}_{part_id}", "part_id": part_id,
        "source_id": source_id, "doc_name": doc_name,
        "file_name": f"{doc_name}.hwics", "doc_type": ["hwics"],
        "parsed_version": "hwics_v1.0", "publish_time": "2026-07-25",
        "product_line": ["云核心网"], "pbi": ["运营商 > 云核心网 > UDG"],
        "path": f"{doc_name}.hwics > L1 > L{part_id}", "title": f"L{part_id}",
        "content": "x", "url": "https://support/hwics.do?nid=x", "public_level": "C",
        "language": "cn",
    }


class FakeSearch:
    """只对 search 端点编程的 MockTransport."""

    def __init__(self):
        self.requests: list[dict] = []
        self.responses: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url == TOKEN_URL:
            return httpx.Response(200, json={"result": "Basic t"})
        body = json.loads(request.content.decode("utf-8"))
        self.requests.append(body)
        resp = self.responses.pop(0) if self.responses else {
            "total": 0, "searchResults": []}
        return httpx.Response(200, json=resp)


def _client(fake: FakeSearch) -> OnenetClient:
    return OnenetClient(
        app_id="a", static_token="s", token_url=TOKEN_URL, search_url=SEARCH_URL,
        transport=httpx.MockTransport(fake.handler),
    )


# ------------------------------------------------------------- search_documents


def test_search_builds_native_and_semantics():
    fake = FakeSearch()
    fake.responses = [{"total": 1, "searchResults": [_doc_slice("DOC1", 1, "UDG")]}]
    c = _client(fake)
    rows = search_documents(c, {
        "doc_name": "UDG", "doc_type": "hwics", "language": "cn",
    })
    assert len(rows) == 1
    body = fake.requests[0]
    ql = body["searchQueryList"]
    assert {"field": "doc_name", "fuzzy": True, "content": "UDG"} in ql
    assert {"field": "doc_type", "fuzzy": False, "content": "hwics"} in ql
    assert {"field": "language", "fuzzy": False, "content": "cn"} in ql


def test_search_dedupes_by_source_id():
    fake = FakeSearch()
    fake.responses = [{
        "total": 3,
        "searchResults": [
            _doc_slice("DOC1", 2, "UDG"), _doc_slice("DOC1", 1, "UDG"),
            _doc_slice("DOC2", 1, "UDM"),
        ],
    }]
    rows = search_documents(_client(fake), {"doc_name": "UDG"})
    assert sorted(r["source_id"] for r in rows) == ["DOC1", "DOC2"]
    d1 = next(r for r in rows if r["source_id"] == "DOC1")
    assert d1["slice_hits"] == 2


def test_search_flags_capped_results():
    fake = FakeSearch()
    fake.responses = [{"total": 10000, "searchResults": [_doc_slice("DOC1", 1, "UDG")]}]
    rows = search_documents(_client(fake), {"doc_name": "UDG"})
    assert rows[0]["capped"] is True


def test_search_rejects_unknown_filter_and_empty():
    c = _client(FakeSearch())
    with pytest.raises(ValueError, match="filters"):
        search_documents(c, {"bad_field": "x"})
    with pytest.raises(ValueError, match="filters"):
        search_documents(c, {})


def test_search_empty_results_returns_empty():
    rows = search_documents(_client(FakeSearch()), {"doc_name": "不存在的文档"})
    assert rows == []


# ------------------------------------------------------------- probe_source


def test_probe_source_card_shape():
    fake = FakeSearch()
    # 依序：count / min / max / 样本3条
    fake.responses = [
        {"total": 157866, "searchResults": []},
        {"total": 1, "searchResults": [_doc_slice("DOC1", 1, "UDG")]},   # asc
        {"total": 1, "searchResults": [_doc_slice("DOC1", 157866, "UDG")]},  # desc
        {"total": 3, "searchResults": [
            _doc_slice("DOC1", 1, "UDG"), _doc_slice("DOC1", 2, "UDG"),
            _doc_slice("DOC1", 3, "UDG")]},
    ]
    card = probe_source(_client(fake), "DOC1")
    assert card["source_id"] == "DOC1"
    assert card["total_slices"] == 157866
    assert card["part_id"] == {"min": 1, "max": 157866}
    assert card["doc_name"] == "UDG"
    assert card["parsed_version"] == "hwics_v1.0"
    assert card["product_line"] == ["云核心网"]
    assert "sample_slices" not in card or card["sample_slices"] == []  # 卡片不带样例正文


def test_probe_source_zero_slices():
    fake = FakeSearch()
    fake.responses = [
        {"total": 0, "searchResults": []},
        {"total": 0, "searchResults": []},  # asc → 空
        {"total": 0, "searchResults": []},  # desc → 空
        {"total": 0, "searchResults": []},  # 样本 → 空
    ]
    card = probe_source(_client(fake), "DOC_NONE")
    assert card["total_slices"] == 0
    assert card["doc_name"] is None
