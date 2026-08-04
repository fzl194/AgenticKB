"""The full-text drill-down tool: scope routing, degradation, and raw-file links.

Same approach as ``test_client_routing``: ``httpx.MockTransport`` rather than mocking our own
functions, so request URLs and payloads are part of what is asserted. Imports only
``mcp_server.client``/``schemas``, never ``server``, so the suite runs without ``mcp`` installed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mcp_server import client as mcp_client
from mcp_server.schemas import FullTextInput, SegmentRef

BASE = mcp_client.BACKEND_URL

BOUND = {
    "domain": "odn",
    "bound": True,
    "paradigmId": "pd-abc",
    "name": "odn-production",
    "version": 3,
}

UNBOUND = {"domain": "odn", "bound": False}

FULLTEXT_BODY = {
    "scope": {"releaseId": "kb:kb-a", "buildId": None, "snapshotCount": 12},
    "items": [
        {
            "ref": {"type": "raw_segment", "id": "seg-1"},
            "found": True,
            "reason": None,
            "unit": None,
            "segments": [
                {
                    "id": "seg-1",
                    "role": "target",
                    "text": "完整原文",
                    "documentId": "doc-7",
                    "documentName": "23501.pdf",
                    "kbId": "kb-a",
                    "hasRawFile": True,
                }
            ],
        }
    ],
}


@pytest.fixture
def calls():
    return []


def install(monkeypatch, handler, calls):
    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(
        mcp_client, "_client", httpx.Client(transport=httpx.MockTransport(recording))
    )
    monkeypatch.setattr(mcp_client, "PARADIGM_ROUTING", True)
    monkeypatch.setattr(mcp_client, "RAW_FILE_BASE_URL", "")


def paths(calls):
    return [c.url.path for c in calls]


def route(*, resolve, fulltext=None):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/paradigm/resolve":
            return resolve(request) if callable(resolve) else resolve
        if path == "/api/v1/segments/fulltext":
            return fulltext(request) if callable(fulltext) else fulltext
        raise AssertionError(f"unexpected request to {path}")

    return handler


def refs(*ids, type="raw_segment"):
    return FullTextInput(domain="odn", refs=[SegmentRef(type=type, id=i) for i in ids])


# ── scope routing ────────────────────────────────────────────────────────


def test_lookup_uses_the_same_paradigm_the_search_used(monkeypatch, calls):
    """The ids came from that paradigm's corpus; looking them up elsewhere would report misses."""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            fulltext=httpx.Response(200, json=FULLTEXT_BODY),
        ),
        calls,
    )

    out = mcp_client.get_segment_fulltext(refs("seg-1"))

    assert paths(calls) == ["/api/v1/paradigm/resolve", "/api/v1/segments/fulltext"]
    payload = json.loads(calls[1].content)
    assert payload["paradigm_id"] == "pd-abc"
    assert payload["domain"] == "odn"
    assert payload["refs"] == [{"type": "raw_segment", "id": "seg-1"}]
    assert out["_retrieval"]["engine"] == "paradigm"


def test_unbound_domain_sends_no_paradigm(monkeypatch, calls):
    """No binding means the search ran on the legacy pipeline too — same corpus either way."""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=UNBOUND),
            fulltext=httpx.Response(200, json=FULLTEXT_BODY),
        ),
        calls,
    )

    mcp_client.get_segment_fulltext(refs("seg-1"))

    assert "paradigm_id" not in json.loads(calls[1].content)


def test_routing_disabled_skips_resolve_entirely(monkeypatch, calls):
    install(
        monkeypatch,
        route(resolve=None, fulltext=httpx.Response(200, json=FULLTEXT_BODY)),
        calls,
    )
    monkeypatch.setattr(mcp_client, "PARADIGM_ROUTING", False)

    mcp_client.get_segment_fulltext(refs("seg-1"))

    assert paths(calls) == ["/api/v1/segments/fulltext"]


def test_resolve_failure_still_looks_up_rather_than_erroring(monkeypatch, calls):
    """Degrades to the active release: yields misses, never content from a KB it should not read."""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(500, text="boom"),
            fulltext=httpx.Response(200, json=FULLTEXT_BODY),
        ),
        calls,
    )

    out = mcp_client.get_segment_fulltext(refs("seg-1"))

    assert "paradigm_id" not in json.loads(calls[1].content)
    assert out["_retrieval"]["engine"] == "legacy"


# ── error handling ───────────────────────────────────────────────────────


def test_empty_refs_never_reaches_the_backend(monkeypatch, calls):
    install(monkeypatch, route(resolve=None, fulltext=None), calls)

    out = mcp_client.get_segment_fulltext(FullTextInput(domain="odn", refs=[]))

    assert out["error"] == "refs_required"
    assert calls == []


def test_backend_error_is_reported_not_raised(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            fulltext=httpx.Response(404, text='{"error":"kb_not_found"}'),
        ),
        calls,
    )

    out = mcp_client.get_segment_fulltext(refs("seg-1"))

    assert out["error"] == "HTTP 404"
    assert "kb_not_found" in out["raw"]


def test_transport_error_is_reported_not_raised(monkeypatch, calls):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/paradigm/resolve":
            return httpx.Response(200, json=UNBOUND)
        raise httpx.ConnectError("connection refused")

    install(monkeypatch, handler, calls)

    out = mcp_client.get_segment_fulltext(refs("seg-1"))

    assert "connection refused" in out["error"]


def test_non_json_response_is_reported(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=UNBOUND),
            fulltext=httpx.Response(200, text="<html>gateway</html>"),
        ),
        calls,
    )

    out = mcp_client.get_segment_fulltext(refs("seg-1"))

    assert out["error"] == "invalid_json_response"


# ── raw file links ───────────────────────────────────────────────────────


def test_no_raw_file_link_unless_a_reachable_base_url_is_configured(monkeypatch, calls):
    """BACKEND_URL is localhost in the container; deriving links from it hands out dead URLs."""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=UNBOUND),
            fulltext=httpx.Response(200, json=FULLTEXT_BODY),
        ),
        calls,
    )

    out = mcp_client.get_segment_fulltext(refs("seg-1"))

    assert "rawFileUrl" not in out["items"][0]["segments"][0]


def test_raw_file_link_added_when_configured(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=UNBOUND),
            fulltext=httpx.Response(200, json=FULLTEXT_BODY),
        ),
        calls,
    )
    monkeypatch.setattr(mcp_client, "RAW_FILE_BASE_URL", "https://kb.example.com")

    out = mcp_client.get_segment_fulltext(refs("seg-1"))

    assert out["items"][0]["segments"][0]["rawFileUrl"] == (
        "https://kb.example.com/api/v1/documents/doc-7/raw?domain=odn"
    )


def test_no_link_for_a_document_without_an_original_file(monkeypatch, calls):
    body = {
        "scope": {"releaseId": "rel-1", "buildId": "b-1", "snapshotCount": 3},
        "items": [
            {
                "ref": {"type": "raw_segment", "id": "seg-1"},
                "found": True,
                "segments": [
                    # Legacy document: ingested through /api/runs, never uploaded to a KB.
                    {"id": "seg-1", "documentId": "doc-legacy", "hasRawFile": False}
                ],
            }
        ],
    }
    install(
        monkeypatch,
        route(resolve=httpx.Response(200, json=UNBOUND), fulltext=httpx.Response(200, json=body)),
        calls,
    )
    monkeypatch.setattr(mcp_client, "RAW_FILE_BASE_URL", "https://kb.example.com")

    out = mcp_client.get_segment_fulltext(refs("seg-1"))

    assert "rawFileUrl" not in out["items"][0]["segments"][0]


def test_a_miss_passes_through_untouched(monkeypatch, calls):
    body = {
        "scope": {"releaseId": "kb:kb-a", "buildId": None, "snapshotCount": 2},
        "items": [
            {
                "ref": {"type": "raw_segment", "id": "seg-gone"},
                "found": False,
                "reason": "out_of_scope",
                "segments": [],
            }
        ],
    }
    install(
        monkeypatch,
        route(resolve=httpx.Response(200, json=BOUND), fulltext=httpx.Response(200, json=body)),
        calls,
    )

    out = mcp_client.get_segment_fulltext(refs("seg-gone"))

    assert out["items"][0]["found"] is False
    assert out["items"][0]["reason"] == "out_of_scope"
