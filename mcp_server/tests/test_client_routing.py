"""Paradigm routing, fallback and response normalization in the MCP client.

Uses httpx.MockTransport rather than mocking our own functions, so the request URLs, the payloads
and the JSON shapes the backend actually returns are all part of what is asserted.

Deliberately imports only ``mcp_server.client``/``schemas`` — not ``server`` — so the suite runs
without the ``mcp`` package installed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mcp_server import client as mcp_client
from mcp_server.schemas import EntityRef, SearchInput

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

# What /api/v1/search returns: pack fields flattened, evidence_groups in snake_case.
LEGACY_BODY = {
    "query": {"original": "SMF 配置"},
    "items": [{"id": "ru-9", "role": "seed"}],
    "relations": [],
    "sources": [],
    "evidence_groups": [],
    "issues": [],
    "suggestions": [],
}

BOUND = {
    "domain": "odn",
    "bound": True,
    "paradigmId": "pd-abc",
    "name": "odn-production",
    "version": 3,
}


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
    monkeypatch.setattr(mcp_client, "PARADIGM_ROUTING", True)


CATALOG_PATH = "/api/v1/paradigm/mcp-catalog"


def paths(calls):
    """The retrieval path taken, excluding the advisory catalog fetch.

    The catalog is a hint attached to the answer, not part of deciding it, and it is cached — so
    whether it appears in a given call says nothing about routing. Tests that care about the fetch
    itself use :func:`all_paths`.
    """
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


def test_bound_domain_goes_to_its_paradigm(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q())

    assert paths(calls) == ["/api/v1/paradigm/resolve", "/api/v1/paradigm/pd-abc/search"]
    assert out["_retrieval"] == {
        "engine": "paradigm",
        "paradigm_id": "pd-abc",
        "name": "odn-production",
        "version": 3,
        "selected_by": "domain_default",
    }


def test_unbound_domain_uses_the_legacy_pipeline(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json={"domain": "generic", "bound": False}),
            search=httpx.Response(200, json=dict(LEGACY_BODY)),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(domain="generic"))

    assert paths(calls) == ["/api/v1/paradigm/resolve", "/api/v1/search"]
    assert out["_retrieval"]["engine"] == "legacy"


def test_resolve_failure_falls_back_instead_of_failing(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(503, text="control db down"),
            search=httpx.Response(200, json=dict(LEGACY_BODY)),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q())

    assert paths(calls) == ["/api/v1/paradigm/resolve", "/api/v1/search"]
    assert out["_retrieval"]["engine"] == "legacy"
    assert "error" not in out


def test_resolve_network_error_falls_back(monkeypatch, calls):
    def boom(request):
        if request.url.path == "/api/v1/paradigm/resolve":
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=dict(LEGACY_BODY))

    install(monkeypatch, boom, calls)

    out = mcp_client.search_knowledge(q())

    assert out["_retrieval"]["engine"] == "legacy"


def test_kill_switch_skips_resolution_entirely(monkeypatch, calls):
    install(
        monkeypatch, route(resolve=None, search=httpx.Response(200, json=dict(LEGACY_BODY))), calls
    )
    monkeypatch.setattr(mcp_client, "PARADIGM_ROUTING", False)

    out = mcp_client.search_knowledge(q())

    assert paths(calls) == ["/api/v1/search"]
    assert out["_retrieval"]["engine"] == "legacy"


def test_paradigm_execution_failure_is_not_retried_on_legacy(monkeypatch, calls):
    """A bound-but-broken paradigm must surface, not be masked by a silent second attempt."""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            paradigm=httpx.Response(400, json={"error": "no_active_kb_build"}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q())

    assert paths(calls) == ["/api/v1/paradigm/resolve", "/api/v1/paradigm/pd-abc/search"]
    assert out["error"] == "HTTP 400"
    assert "no_active_kb_build" in out["raw"]
    assert out["_retrieval"]["engine"] == "paradigm"


def test_bound_true_without_an_id_is_treated_as_unbound(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json={"domain": "odn", "bound": True}),
            search=httpx.Response(200, json=dict(LEGACY_BODY)),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q())

    assert out["_retrieval"]["engine"] == "legacy"


# ── normalization ────────────────────────────────────────────────────────


def test_paradigm_response_matches_the_legacy_envelope(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q())

    # The whole point: an agent must not be able to tell the two engines apart by shape.
    assert set(LEGACY_BODY) <= set(out)
    assert "contextPack" not in out
    assert "evidenceGroups" not in out
    assert out["evidence_groups"] == [{"documentSnapshotId": "snap-1", "itemIds": ["ru-1"]}]
    assert out["items"][0]["id"] == "ru-1"
    # nested field naming is untouched — both engines serialize the same records
    assert out["items"][0]["blockType"] == "paragraph"


def test_missing_pack_sections_default_to_empty_lists(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            paradigm=httpx.Response(200, json={"contextPack": {"items": []}}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q())

    for key in ("items", "relations", "sources", "evidence_groups", "issues", "suggestions"):
        assert out[key] == [], key


def test_debug_trace_is_parked_under_retrieval(monkeypatch, calls):
    body = dict(PARADIGM_BODY)
    body["trace"] = [{"nodeId": "dv", "operatorType": "dense_vector", "durationMs": 42}]
    install(
        monkeypatch,
        route(resolve=httpx.Response(200, json=BOUND), paradigm=httpx.Response(200, json=body)),
        calls,
    )

    out = mcp_client.search_knowledge(q(debug=True))

    assert out["_retrieval"]["trace"][0]["nodeId"] == "dv"
    assert "trace" not in out


def test_candidate_only_paradigm_is_passed_through_and_flagged(monkeypatch, calls):
    """Binding forbids collect-terminated paradigms; if one shows up anyway, say so."""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            paradigm=httpx.Response(200, json={"candidates": [{"id": "ru-1", "score": 0.9}]}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q())

    assert out["candidates"][0]["id"] == "ru-1"
    assert out["_retrieval"]["output"] == "candidates"


# ── ignored args ─────────────────────────────────────────────────────────


def test_dead_tool_args_are_reported_on_the_paradigm_path(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(
        q(scope={"product": "X"}, entities=[EntityRef(name="SMF")])
    )

    assert out["_retrieval"]["ignored_args"] == ["scope", "entities"]


def test_dead_tool_args_are_reported_on_the_legacy_path_too(monkeypatch, calls):
    """They have never been consumed there either — reporting only on one path would mislead."""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json={"bound": False}),
            search=httpx.Response(200, json=dict(LEGACY_BODY)),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(scope={"product": "X"}))

    assert out["_retrieval"] == {
        "engine": "legacy",
        "selected_by": "fallback",
        "ignored_args": ["scope"],
    }


def test_no_ignored_args_key_when_nothing_was_dropped(monkeypatch, calls):
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q())

    assert "ignored_args" not in out["_retrieval"]


# ── request payload ──────────────────────────────────────────────────────


def test_paradigm_request_body_carries_query_domain_debug(monkeypatch, calls):
    import json

    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    mcp_client.search_knowledge(q(debug=True))

    # Selected by path, not by position: the advisory catalog fetch also lands in `calls`, and
    # positional indexing would silently start asserting against whichever request happened to be
    # last.
    executed = [c for c in calls if c.url.path.endswith("/search")]
    assert len(executed) == 1
    body = json.loads(executed[0].content)
    assert body == {"query": "SMF 配置", "domain": "odn", "debug": True}

    resolves = [c for c in calls if c.url.path == "/api/v1/paradigm/resolve"]
    assert resolves[0].url.params["domain"] == "odn"


# ── regression guard for the paradigm-selection change ───────────────────


def test_request_without_a_named_paradigm_is_byte_identical(monkeypatch, calls):
    """The no-``paradigm`` path must keep issuing exactly the requests it always did.

    Written before the ``paradigm`` parameter existed and kept green through it: everything about
    agent-visible behaviour is allowed to grow, but a caller that names no paradigm must reach the
    same endpoints with the same bodies as before, or the change stops being additive.

    Asserts the *requests*, not the response — ``_retrieval`` gains fields by design.
    """
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json={"domain": "generic", "bound": False}),
            search=httpx.Response(200, json=dict(LEGACY_BODY)),
        ),
        calls,
    )

    mcp_client.search_knowledge(q(domain="generic", query="AA 接口"))

    searches = [c for c in calls if c.url.path == "/api/v1/search"]
    assert len(searches) == 1
    assert json.loads(searches[0].content) == {
        "query": "AA 接口",
        "domain": "generic",
        "debug": False,
    }

    resolves = [c for c in calls if c.url.path == "/api/v1/paradigm/resolve"]
    assert len(resolves) == 1
    assert resolves[0].url.params["domain"] == "generic"


def test_bound_paradigm_request_is_byte_identical(monkeypatch, calls):
    """Same guard for the domain-default path: same URL, same body."""
    install(
        monkeypatch,
        route(
            resolve=httpx.Response(200, json=BOUND),
            paradigm=httpx.Response(200, json=PARADIGM_BODY),
        ),
        calls,
    )

    mcp_client.search_knowledge(q(query="AA 接口"))

    executed = [c for c in calls if c.url.path.endswith("/search") and "paradigm" in c.url.path]
    assert len(executed) == 1
    assert executed[0].url.path == "/api/v1/paradigm/pd-abc/search"
    assert json.loads(executed[0].content) == {
        "query": "AA 接口",
        "domain": "odn",
        "debug": False,
    }
