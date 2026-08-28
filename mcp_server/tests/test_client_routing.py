"""阶段 A（批次5）后的 MCP 路由与响应归一化测试。

库为中心四层路由：显式范式 > 库级绑定（resolve kbIds）> 领域默认 > 官方默认；
无 legacy 回落——unbound/resolve 失败一律报 no_paradigm_configured。
所有 serving 调用必须携带 X-KB-User 与请求级 kbIds。

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

BASE = mcp_client.BACKEND_URL

# What /api/v1/paradigm/{id}/search returns: the pack wrapped one level down, camelCase.
PARADIGM_BODY = {
    "contextPack": {
        "query": {"original": "SMF 配置", "intent": "howto", "snapshotCount": 12},
        "items": [{"id": "ru-1", "role": "seed", "text": "…", "blockType": "paragraph"}],
        "relations": [],
        "sources": [{"id": "doc-1", "documentKey": "doc:/a.md"}],
        "evidenceGroups": [{"documentSnapshotId": "snap-1", "itemIds": ["ru-1"]}],
        "issues": [],
        "suggestions": [],
        "debug": {},
    }
}

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

BOUND_DEGRADED = {
    **BOUND_DOMAIN,
    "source": "domain",
    "degraded": True,
    "degradedFrom": "library",
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
        if path == "/api/v1/paradigm/mcp-catalog":
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
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), ["kb-1"])

    assert paths(calls) == ["/api/v1/paradigm/resolve", "/api/v1/paradigm/pd-abc/search"]
    assert out["_retrieval"] == {
        "engine": "paradigm",
        "paradigm_id": "pd-abc",
        "name": "odn-production",
        "version": 3,
        "selected_by": "domain",
    }
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
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), ["kb-1", "kb-2"])

    assert out["_retrieval"]["selected_by"] == "library"
    assert out["_retrieval"]["paradigm_id"] == "pd-lib"


def test_library_degradation_is_surfaced_in_selected_by(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DEGRADED),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), ["kb-1"])

    assert out["_retrieval"]["selected_by"] == "domain(degraded_from_library)"


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
    assert out["_retrieval"]["selected_by"] == "unresolved"
    assert out["_retrieval"]["engine"] == "none"


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
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
            catalog=catalog,
        ),
        calls,
    )

    out = mcp_client.search_knowledge(
        q(paradigm="odn-production"), ident(), ["kb-1"]
    )

    assert paths(calls) == ["/api/v1/paradigm/pd-abc/search"]
    assert out["_retrieval"]["selected_by"] == "explicit"
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
    assert out["_retrieval"]["selected_by"] == "rejected"
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
    assert out["_retrieval"]["engine"] == "paradigm"


# ── request payload ──────────────────────────────────────────────────────


def test_paradigm_request_body_carries_query_domain_debug(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
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
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    mcp_client.search_knowledge(q(), ident(), [])

    search_call = [c for c in calls if c.url.path.endswith("/search")][0]
    assert "kbIds" not in json.loads(search_call.content)


# ── response normalization ───────────────────────────────────────────────


def test_context_pack_is_flattened_to_the_search_shape(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert out["query"] == PARADIGM_BODY["contextPack"]["query"]
    assert out["items"] == PARADIGM_BODY["contextPack"]["items"]
    assert out["evidence_groups"] == PARADIGM_BODY["contextPack"]["evidenceGroups"]
    for key in ("items", "relations", "sources", "evidence_groups", "issues", "suggestions"):
        assert key in out


def test_candidates_output_is_flagged_not_passsed_silently(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json={"candidates": [{"id": "ru-1"}]}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert out["_retrieval"]["output"] == "candidates"
    assert out["candidates"] == [{"id": "ru-1"}]


def test_missing_context_pack_is_visible(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json={}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert out["error"] == "empty_paradigm_result"


# ── dead args ────────────────────────────────────────────────────────────


def test_dead_tool_args_are_reported_on_the_paradigm_path(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(
        q(scope={"产品": "OLT"}, entities=None), ident(), []
    )

    assert out["_retrieval"]["ignored_args"] == ["scope"]


def test_no_ignored_args_key_when_nothing_was_dropped(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND_DOMAIN),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert "ignored_args" not in out["_retrieval"]
