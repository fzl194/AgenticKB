"""批次8 R7/R8：serving 结构工具族的转发面（tools.py）。

get_evidence / get_document / inspect_knowledge / navigate_structure /
query_structured_asset 经 serving internal REST（X-Internal-Auth 独立密钥）转发；
typed error（25 号 §7.2）以 ServingToolError 原样上抛供工具层转成 Agent 可修正信息。

Uses httpx.MockTransport: the asserted URLs, headers, and payloads are the ones serving
would really see.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mcp_server import tools as backend


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("SERVING_INTERNAL_AUTH_SECRET", "serving-internal-secret")
    monkeypatch.setattr(backend, "SERVING_INTERNAL_URL", "http://serving-test:8081")


@pytest.fixture
def calls():
    return []


def install(monkeypatch, handler, calls):
    """Patch tools 层使用的 httpx.post → MockTransport handler（记录每个请求）。"""

    def patched_post(url, *, json=None, headers=None, timeout=None, trust_env=False):
        request = httpx.Request("POST", url, json=json, headers=headers or {})
        return handler(request)

    monkeypatch.setattr(httpx, "post", patched_post)
    return patched_post


def ok(body: dict):
    return httpx.Response(200, json=body)


def typed_error(status: int, code: str, message: str, details: dict | None = None):
    err: dict = {"code": code, "message": message}
    if details:
        err["details"] = details
    return httpx.Response(status, json={"error": err})


def handler_for(calls, responder):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responder(request)
    return handler


# ── happy path ────────────────────────────────────────────────────────────


def test_get_evidence_forwards_with_internal_auth(monkeypatch, calls):
    def responder(request):
        assert request.url.path == "/api/internal/evidence/ev_abc"
        return ok({"ref": "ev_abc", "type": "prose", "content": "完整原文"})

    install(monkeypatch, handler_for(calls, responder), calls)

    out = backend.get_evidence("alice", ["kb-1"], "odn", "ev_abc", mode="parent")

    assert out["content"] == "完整原文"
    req = calls[0]
    assert req.headers["X-Internal-Auth"] == "serving-internal-secret"
    body = json.loads(req.content)
    assert body == {"domain": "odn", "kb_ids": ["kb-1"], "username": "alice", "mode": "parent"}


def test_get_document_forwards_ref_and_pagination(monkeypatch, calls):
    def responder(request):
        assert request.url.path == "/api/internal/document/doc_x"
        return ok({"document_ref": "doc_x", "segments": [], "has_more": True, "cursor": "bz0xMTA="})

    install(monkeypatch, handler_for(calls, responder), calls)

    out = backend.get_document("alice", ["kb-1"], "odn", "doc_x", limit=50, cursor="bz0xNTA=")

    body = json.loads(calls[0].content)
    assert body["limit"] == 50
    assert body["cursor"] == "bz0xNTA="
    assert out["has_more"] is True


def test_inspect_forwards_any_ref_kind(monkeypatch, calls):
    def responder(request):
        assert request.url.path == "/api/internal/inspect"
        return ok({"ref_kind": "structure_ref", "capabilities": {"can_navigate": True}})

    install(monkeypatch, handler_for(calls, responder), calls)

    out = backend.inspect_knowledge("alice", ["kb-1"], "odn", "st_abc")

    body = json.loads(calls[0].content)
    assert body["ref"] == "st_abc"
    assert out["capabilities"]["can_navigate"] is True


def test_navigate_structure_forwards_relation_and_bounds(monkeypatch, calls):
    def responder(request):
        assert request.url.path == "/api/internal/navigate"
        return ok({"relation": "children", "nodes": [], "has_more": False})

    install(monkeypatch, handler_for(calls, responder), calls)

    backend.navigate_structure("alice", ["kb-1"], "odn", "st_abc", "children",
                               depth=2, limit=100, cursor=None)

    body = json.loads(calls[0].content)
    assert body == {"domain": "odn", "kb_ids": ["kb-1"], "username": "alice",
                    "ref": "st_abc", "relation": "children", "depth": 2, "limit": 100}


def test_query_structured_asset_forwards_dsl(monkeypatch, calls):
    def responder(request):
        assert request.url.path == "/api/internal/structured-query"
        return ok({"rows": [{"型号": "OLT-1"}], "has_more": False})

    install(monkeypatch, handler_for(calls, responder), calls)

    dsl = {"select": ["型号"], "where": [{"field": "最大功耗", "op": "lte", "value": 100}]}
    out = backend.query_structured_asset("alice", ["kb-1"], "odn", "st_tbl", dsl)

    body = json.loads(calls[0].content)
    assert body["ref"] == "st_tbl"
    assert body["query"] == dsl
    assert out["rows"][0]["型号"] == "OLT-1"


# ── typed errors（§7.2 原样上抛） ─────────────────────────────────────────


@pytest.mark.parametrize("status,code", [
    (400, "unknown_field"),
    (404, "out_of_scope"),
    (410, "expired_ref"),
    (409, "structured_query_unavailable"),
    (413, "result_too_large"),
])
def test_typed_errors_surface_code_and_details(monkeypatch, calls, status, code):
    def responder(request):
        return typed_error(status, code, "人类可读信息", {"allowed_fields": ["型号"]})

    install(monkeypatch, handler_for(calls, responder), calls)

    with pytest.raises(backend.ServingToolError) as exc_info:
        backend.query_structured_asset("alice", ["kb-1"], "odn", "st_tbl", {})

    assert exc_info.value.code == code
    assert "人类可读信息" in str(exc_info.value)
    assert exc_info.value.details == {"allowed_fields": ["型号"]}


def test_untyped_http_error_is_backend_error(monkeypatch, calls):
    def responder(request):
        return httpx.Response(500, json={"detail": "boom"})

    install(monkeypatch, handler_for(calls, responder), calls)

    with pytest.raises(backend.ToolBackendError) as exc_info:
        backend.get_evidence("alice", ["kb-1"], "odn", "ev_x")
    assert not isinstance(exc_info.value, backend.ServingToolError)


def test_missing_secret_refuses(monkeypatch, calls):
    import os
    monkeypatch.delenv("SERVING_INTERNAL_AUTH_SECRET", raising=False)
    monkeypatch.setattr(backend, "_serving_secret", lambda: "")

    def responder(request):  # pragma: no cover - must not be called
        raise AssertionError("no request may leave without a secret")

    install(monkeypatch, handler_for(calls, responder), calls)

    with pytest.raises(backend.ToolBackendError, match="内部鉴权"):
        backend.get_evidence("alice", ["kb-1"], "odn", "ev_x")
    assert calls == []


def test_unreachable_serving_is_a_clean_backend_error(monkeypatch, calls):
    def responder(request):
        raise httpx.ConnectError("serving down")

    install(monkeypatch, handler_for(calls, responder), calls)

    with pytest.raises(backend.ToolBackendError, match="暂不可用"):
        backend.navigate_structure("alice", ["kb-1"], "odn", "st_x", "children")
