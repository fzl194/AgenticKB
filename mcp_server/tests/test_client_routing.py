"""批次8 R8：MCP 检索路由与 EvidenceResponse 纯协议。

库为中心路由（显式范式 > 库级绑定 > 官方默认）不变；响应切纯 EvidenceResponse
（query/evidence/has_more，25 号 §5.3）——无 _retrieval、无 ContextPack 归一化、
无内部 id 回传。显式 within/filters/top_k/expansion 按 §7.1 语义透传。

Uses httpx.MockTransport rather than mocking our own functions, so the request URLs, the payloads
and the JSON shapes the backend actually returns are all part of what is asserted.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mcp_server import client as mcp_client
from mcp_server.identity import Identity
from mcp_server.schemas import SearchInput

BOUND_DOMAIN = {
    "domain": "odn",
    "bound": True,
    "paradigmId": "pd-abc",
    "name": "odn-production",
    "version": 3,
    "source": "domain",
}

BOUND_LIBRARY = {
    **BOUND_DOMAIN,
    "paradigmId": "pd-lib",
    "name": "kb-default",
    "source": "library",
}

#: serving /api/v1/paradigm/{id}/search 的成功响应（R6+：evidenceResponse 终点）。
EVIDENCE_BODY = {
    "evidenceResponse": {
        "query": "SMF 配置",
        "evidence": [
            {
                "ref": "ev_abc123",
                "type": "prose",
                "content": "配置 SMF 需要先…",
                "source": {
                    "knowledge_base": "基站手册库",
                    "file_name": "23501.pdf",
                    "document_ref": "doc_def456",
                    "section": "第三章/配置",
                },
                "truncated": False,
                "structure_ref": "st_ghi789",
            }
        ],
        "has_more": False,
    }
}


def ident() -> Identity:
    return Identity(
        username="alice",
        user_id="u-1",
        open_kbs=(
            {"id": "kb-1", "name": "基站手册库"},
            {"id": "kb-2", "name": "设备手册库"},
        ),
    )


@pytest.fixture
def calls():
    return []


def install(monkeypatch, handler, calls):
    """Point the module-level client at a MockTransport and record every request."""

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(
        mcp_client, "_client", httpx.Client(transport=httpx.MockTransport(recording))
    )


CATALOG_PATH = "/api/v1/paradigm/mcp-catalog"


def paths(calls):
    """The retrieval path taken, excluding the advisory catalog fetch."""
    return [c.url.path for c in calls if c.url.path != CATALOG_PATH]


def all_paths(calls):
    return [c.url.path for c in calls]


def route(*, resolve, search=None, paradigm=None, catalog=None):
    """Build a handler from per-endpoint canned responses.

    ``catalog`` defaults to a 503: tests that say nothing about the catalog are asserting the
    behaviour of everything else, and an unreachable catalog is the case where those assertions
    must hold unchanged.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == CATALOG_PATH:
            if catalog is None:
                return httpx.Response(503, text="catalog not stubbed for this test")
            return catalog(request) if callable(catalog) else catalog
        if path == "/api/v1/paradigm/resolve":
            return resolve(request) if callable(resolve) else resolve
        if path.startswith("/api/v1/paradigm/") and path.endswith("/search"):
            return paradigm(request) if callable(paradigm) else paradigm
        if path == "/api/v1/search":
            return search(request) if callable(search) else search
        raise AssertionError(f"unexpected request to {path}")

    return handler


def q(**kw):
    return SearchInput(query=kw.pop("query", "SMF 配置"), domain=kw.pop("domain", "odn"), **kw)


# ── routing ──────────────────────────────────────────────────────────────


def test_domain_default_goes_to_its_paradigm_with_identity(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json=EVIDENCE_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), ["kb-1"])

    assert paths(calls) == ["/api/v1/paradigm/resolve", "/api/v1/paradigm/pd-abc/search"]
    # 身份透传与请求级库范围是硬契约
    search_call = [c for c in calls if c.url.path.endswith("/search")][0]
    assert search_call.headers["X-KB-User"] == "alice"
    assert json.loads(search_call.content)["kbIds"] == ["kb-1"]
    resolve_call = [c for c in calls if c.url.path.endswith("/resolve")][0]
    assert "kbIds=kb-1" in str(resolve_call.url)


def test_library_binding_wins_when_consistent(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_LIBRARY),
            paradigm=httpx.Response(200, json=EVIDENCE_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), ["kb-1", "kb-2"])

    assert "/api/v1/paradigm/pd-lib/search" in paths(calls)
    assert out == EVIDENCE_BODY["evidenceResponse"]


def test_unbound_domain_reports_no_paradigm_configured(monkeypatch, calls):
    """无任何绑定 → 明确报错；绝不回落 /api/v1/search（阶段 A 拍板）。"""
    install(
        monkeypatch,
        route(resolve=httpx.Response(200, json={"domain": "generic", "bound": False})),
        calls,
    )

    out = mcp_client.search_knowledge(q(domain="generic"), ident(), [])

    assert paths(calls) == ["/api/v1/paradigm/resolve"]
    assert out["error"] == "no_paradigm_configured"
    assert "message" in out


def test_resolve_failure_reports_instead_of_falling_back(monkeypatch, calls):
    install(
        monkeypatch,
        route(resolve=httpx.Response(503, text="control db down")),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), ["kb-1"])

    assert paths(calls) == ["/api/v1/paradigm/resolve"]
    assert out["error"] == "no_paradigm_configured"


def test_resolve_network_error_reports(monkeypatch, calls):
    def boom(request):
        if request.url.path == "/api/v1/paradigm/resolve":
            raise httpx.ConnectError("control db down")
        if request.url.path == CATALOG_PATH:
            return httpx.Response(503, text="catalog not stubbed for this test")
        raise AssertionError(f"unexpected request to {request.url.path}")

    install(monkeypatch, boom, calls)

    out = mcp_client.search_knowledge(q(), ident(), ["kb-1"])

    assert out["error"] == "no_paradigm_configured"
    assert "error" in out and "message" in out


def test_explicit_paradigm_skips_resolve(monkeypatch, calls):
    catalog = httpx.Response(
        200,
        json={"paradigms": [{"id": "pd-abc", "name": "odn-production", "domain": "odn"}]},
    )
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(500, text="resolve must not be called"),
            paradigm=httpx.Response(200, json=EVIDENCE_BODY),
            catalog=catalog,
        ),
        calls,
    )

    out = mcp_client.search_knowledge(
        q(paradigm="odn-production"), ident(), ["kb-1"]
    )

    assert paths(calls) == ["/api/v1/paradigm/pd-abc/search"]
    assert out == EVIDENCE_BODY["evidenceResponse"]
    # 显式范式仍带身份与库范围（范式 scope 留空时按注入执行）
    search_call = [c for c in calls if c.url.path.endswith("/search")][0]
    assert search_call.headers["X-KB-User"] == "alice"
    assert json.loads(search_call.content)["kbIds"] == ["kb-1"]


def test_explicit_unknown_paradigm_never_falls_back(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(500, text="resolve must not be called"),
            paradigm=httpx.Response(500, text="search must not be called"),
            catalog=httpx.Response(200, json={"paradigms": []}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(paradigm="ghost"), ident(), [])

    assert out["error"] == "unknown_paradigm"
    # 缓存刷新会拉两次 catalog，但绝不发出 search/resolve
    assert paths(calls) == []
    assert all_paths(calls) == [CATALOG_PATH, CATALOG_PATH]


def test_paradigm_http_failure_is_returned_not_swallowed(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(502, text="bad gateway"),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert out["error"] == "HTTP 502"


# ── request payload（§7.1 透传） ──────────────────────────────────────────


def test_paradigm_request_body_carries_query_domain_debug(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json=EVIDENCE_BODY),
        ),
        calls,
    )

    mcp_client.search_knowledge(q(query="端口镜像怎么配", debug=True), ident(), [])

    search_call = [c for c in calls if c.url.path.endswith("/search")][0]
    body = json.loads(search_call.content)
    assert body == {"query": "端口镜像怎么配", "domain": "odn", "debug": True}


def test_kb_ids_omitted_when_empty(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json=EVIDENCE_BODY),
        ),
        calls,
    )

    mcp_client.search_knowledge(q(), ident(), [])

    search_call = [c for c in calls if c.url.path.endswith("/search")][0]
    assert "kbIds" not in json.loads(search_call.content)


def test_explicit_constraints_are_forwarded_as_hard_filters(monkeypatch, calls):
    """§7.1：显式 within/filters/expansion/top_k 原样透传（显式传入 = hard filter）。"""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json=EVIDENCE_BODY),
        ),
        calls,
    )

    mcp_client.search_knowledge(
        q(
            within={"document_refs": ["doc_a"], "include_descendants": True},
            filters={"evidence_types": ["table_row"]},
            expansion={"mode": "parent"},
            top_k=15,
        ),
        ident(),
        ["kb-1"],
    )

    search_call = [c for c in calls if c.url.path.endswith("/search")][0]
    body = json.loads(search_call.content)
    assert body["within"] == {"document_refs": ["doc_a"], "include_descendants": True}
    assert body["filters"] == {"evidence_types": ["table_row"]}
    assert body["expansion"] == {"mode": "parent"}
    assert body["top_k"] == 15


def test_absent_constraints_send_no_keys(monkeypatch, calls):
    """未传 = 宽检索：不发 within/filters/expansion/top_k 键（服务端不加隐式约束）。"""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json=EVIDENCE_BODY),
        ),
        calls,
    )

    mcp_client.search_knowledge(q(), ident(), [])

    search_call = [c for c in calls if c.url.path.endswith("/search")][0]
    body = json.loads(search_call.content)
    for key in ("within", "filters", "expansion", "top_k"):
        assert key not in body


# ── response protocol（§5.3 纯 EvidenceResponse） ────────────────────────


def test_success_is_the_pure_evidence_response(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json=EVIDENCE_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert out == {
        "query": "SMF 配置",
        "evidence": EVIDENCE_BODY["evidenceResponse"]["evidence"],
        "has_more": False,
    }
    # §13.3 硬约束：正常响应不得出现内部检索元数据
    assert "_retrieval" not in out
    assert "candidates" not in out
    assert "contextPack" not in out


def test_non_evidence_output_is_flagged_not_passed_silently(monkeypatch, calls):
    """范式终点不是 assemble（发布质量缺陷）→ 明确报错，不冒充证据列表。"""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json={"candidates": [{"id": "ru-1"}]}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert out["error"] == "no_evidence_response"
    assert "candidates" not in out


def test_empty_body_is_visible(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json={}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert out["error"] == "no_evidence_response"
