"""Naming a paradigm explicitly: resolution, its four failure modes, and the advisory catalog.

Uses ``httpx.MockTransport`` like the other suites, so the asserted URLs and payloads are the ones
the backend would really see.

The theme running through every failure case: an explicit selection is never quietly served by
another engine. The caller asserted a choice, and answering from something else looks exactly like
having honoured it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mcp_server import client as mcp_client
from mcp_server.schemas import FullTextInput, SearchInput, SegmentRef

CATALOG_PATH = "/api/v1/paradigm/mcp-catalog"

ODN_TOPOLOGY = {
    "id": "pd-3f2a1b7c",
    "name": "ODN 拓扑排障",
    "description": "查 ODN 拓扑与端口占用",
    "domain": "odn",
    "version": 3,
    "isDomainDefault": True,
}
ODN_TABLES = {
    "id": "pd-9c11ab02",
    "name": "ODN 参数表查询",
    "description": "偏向表格行的检索",
    "domain": "odn",
    "version": 1,
    "isDomainDefault": False,
}
CCN_ONLY = {
    "id": "pd-77de01f4",
    "name": "核心网配置",
    "description": "",
    "domain": "cloud_core_network",
    "version": 2,
    "isDomainDefault": False,
}
ANY_DOMAIN = {
    "id": "pd-aaaa0000",
    "name": "通用检索",
    "description": "不限知识域",
    "domain": None,
    "version": 1,
    "isDomainDefault": False,
}

PACK = {
    "contextPack": {
        "query": {"original": "光路不通"},
        "items": [{"id": "ru-1", "role": "seed"}],
        "relations": [],
        "sources": [],
        "evidenceGroups": [],
        "issues": [],
        "suggestions": [],
    }
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


def backend(*, catalog, paradigm=None, resolve=None, search=None):
    """Handler over the four endpoints this suite touches."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == CATALOG_PATH:
            return catalog(request) if callable(catalog) else catalog
        if path == "/api/v1/paradigm/resolve":
            return resolve or httpx.Response(200, json={"bound": False})
        if path.startswith("/api/v1/paradigm/") and path.endswith("/search"):
            return paradigm or httpx.Response(200, json=PACK)
        if path == "/api/v1/segments/fulltext":
            return httpx.Response(200, json={"scope": {}, "items": []})
        if path == "/api/v1/search":
            return search or httpx.Response(200, json={"items": []})
        raise AssertionError(f"unexpected request to {path}")

    return handler


def catalog_of(*entries):
    return httpx.Response(200, json={"paradigms": list(entries)})


def q(**kw):
    return SearchInput(query=kw.pop("query", "光路不通"), domain=kw.pop("domain", "odn"), **kw)


def executed_paradigm(calls):
    hits = [c for c in calls if c.url.path.startswith("/api/v1/paradigm/pd-")]
    return hits[0].url.path if hits else None


# ── resolving a named paradigm ───────────────────────────────────────────


def test_selects_by_name(monkeypatch, calls):
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY, ODN_TABLES)), calls)

    out = mcp_client.search_knowledge(q(paradigm="ODN 参数表查询"))

    assert executed_paradigm(calls) == "/api/v1/paradigm/pd-9c11ab02/search"
    assert out["_retrieval"]["selected_by"] == "explicit"
    assert out["_retrieval"]["paradigm_id"] == "pd-9c11ab02"


def test_selects_by_id(monkeypatch, calls):
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY, ODN_TABLES)), calls)

    mcp_client.search_knowledge(q(paradigm="pd-3f2a1b7c"))

    assert executed_paradigm(calls) == "/api/v1/paradigm/pd-3f2a1b7c/search"


@pytest.mark.parametrize("named", ["  ODN 参数表查询  ", "odn 参数表查询", "PD-9C11AB02"])
def test_matching_tolerates_copy_paste(monkeypatch, calls, named):
    """Agents copy these strings out of a previous answer; whitespace and case ride along."""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY, ODN_TABLES)), calls)

    mcp_client.search_knowledge(q(paradigm=named))

    assert executed_paradigm(calls) == "/api/v1/paradigm/pd-9c11ab02/search"


def test_naming_a_paradigm_skips_the_domain_default_lookup(monkeypatch, calls):
    """The tool IS the choice; re-resolving could only disagree with it."""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY)), calls)

    mcp_client.search_knowledge(q(paradigm="ODN 拓扑排障"))

    assert "/api/v1/paradigm/resolve" not in [c.url.path for c in calls]


# ── the four failure modes ───────────────────────────────────────────────


def test_unknown_name_is_rejected_not_silently_redirected(monkeypatch, calls):
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY)), calls)

    out = mcp_client.search_knowledge(q(paradigm="不存在的范式"))

    assert out["error"] == "unknown_paradigm"
    assert "ODN 拓扑排障" in out["message"], "the agent needs the valid options to self-correct"
    assert executed_paradigm(calls) is None
    assert "/api/v1/search" not in [c.url.path for c in calls], "must not fall back to legacy"
    assert out["_retrieval"]["selected_by"] == "rejected"


def test_a_freshly_published_paradigm_forces_a_refresh_before_being_declared_unknown(
    monkeypatch, calls
):
    """Publish-then-immediately-use must work, whatever the cache happens to hold."""
    responses = [catalog_of(ODN_TOPOLOGY), catalog_of(ODN_TOPOLOGY, ODN_TABLES)]

    def catalog(_request):
        return responses.pop(0) if len(responses) > 1 else responses[0]

    install(monkeypatch, backend(catalog=catalog), calls)

    out = mcp_client.search_knowledge(q(paradigm="ODN 参数表查询"))

    assert [c.url.path for c in calls].count(CATALOG_PATH) >= 2, "stale miss must re-fetch"
    assert executed_paradigm(calls) == "/api/v1/paradigm/pd-9c11ab02/search"
    assert "error" not in out


def test_domain_mismatch_names_both_domains(monkeypatch, calls):
    """Executing it would fail deep inside scope_resolve as kb_not_found — a misleading error."""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY, CCN_ONLY)), calls)

    out = mcp_client.search_knowledge(q(domain="odn", paradigm="核心网配置"))

    assert out["error"] == "paradigm_domain_mismatch"
    assert "cloud_core_network" in out["message"]
    assert "odn" in out["message"]
    assert executed_paradigm(calls) is None


def test_catalog_down_passes_an_id_through_but_rejects_a_name(monkeypatch, calls):
    install(monkeypatch, backend(catalog=httpx.Response(503, text="down")), calls)

    by_id = mcp_client.search_knowledge(q(paradigm="pd-3f2a1b7c"))
    assert executed_paradigm(calls) == "/api/v1/paradigm/pd-3f2a1b7c/search"
    assert "error" not in by_id

    calls.clear()
    by_name = mcp_client.search_knowledge(q(paradigm="ODN 拓扑排障"))
    assert by_name["error"] == "catalog_unavailable", (
        "a name cannot be resolved without the catalog, and 'unknown' would be a lie"
    )
    assert executed_paradigm(calls) is None
    assert "/api/v1/search" not in [c.url.path for c in calls]


# ── the advisory list ────────────────────────────────────────────────────


def test_every_answer_carries_the_available_paradigms(monkeypatch, calls):
    """Discovery as a by-product: one call and the agent knows what else it could have asked for."""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY, ODN_TABLES)), calls)

    out = mcp_client.search_knowledge(q())

    assert out["_retrieval"]["available_paradigms"] == [
        {"name": "ODN 拓扑排障", "description": "查 ODN 拓扑与端口占用"},
        {"name": "ODN 参数表查询", "description": "偏向表格行的检索"},
    ]


def test_the_list_is_attached_to_errors_too(monkeypatch, calls):
    """A domain with no active release fails its very first unnamed call. Without the list on the
    error path the agent would have nothing to correct towards."""
    install(
        monkeypatch,
        backend(
            catalog=catalog_of(ODN_TOPOLOGY),
            search=httpx.Response(400, json={"error": "no_active_release"}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q())

    assert out["error"] == "HTTP 400"
    assert "no_active_release" in out["raw"]
    assert [e["name"] for e in out["_retrieval"]["available_paradigms"]] == ["ODN 拓扑排障"]


def test_the_list_is_filtered_to_this_domain(monkeypatch, calls):
    """Offering a paradigm bound elsewhere would only earn a paradigm_domain_mismatch."""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY, CCN_ONLY, ANY_DOMAIN)), calls)

    out = mcp_client.search_knowledge(q(domain="odn"))

    assert [e["name"] for e in out["_retrieval"]["available_paradigms"]] == [
        "ODN 拓扑排障",
        "通用检索",
    ]


def test_an_unreachable_catalog_omits_the_field_rather_than_reporting_none(monkeypatch, calls):
    """Absent and empty must not look alike: empty would read as 'there are no paradigms'."""
    install(monkeypatch, backend(catalog=httpx.Response(503, text="down")), calls)

    out = mcp_client.search_knowledge(q())

    assert "available_paradigms" not in out["_retrieval"]
    assert "error" not in out, "a hint that could not be fetched must not fail the search"


def test_the_catalog_is_cached_across_calls(monkeypatch, calls):
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY)), calls)

    mcp_client.search_knowledge(q())
    mcp_client.search_knowledge(q())

    assert [c.url.path for c in calls].count(CATALOG_PATH) == 1


def test_an_expired_cache_refetches(monkeypatch, calls):
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY)), calls)
    monkeypatch.setattr(mcp_client, "CATALOG_TTL", 0.0)

    mcp_client.search_knowledge(q())
    mcp_client.search_knowledge(q())

    assert [c.url.path for c in calls].count(CATALOG_PATH) == 2


# ── pairing with the drill-down ──────────────────────────────────────────


def test_fulltext_honours_an_explicit_paradigm_id(monkeypatch, calls):
    """Without this the lookup re-resolves the domain default — a different set of knowledge
    bases — and every id comes back found:false for a reason that reads like 're-mined'."""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY)), calls)

    mcp_client.get_segment_fulltext(
        FullTextInput(
            domain="odn",
            refs=[SegmentRef(type="raw_segment", id="seg-1")],
            paradigm_id="pd-9c11ab02",
        )
    )

    posts = [c for c in calls if c.url.path == "/api/v1/segments/fulltext"]
    assert json.loads(posts[0].content)["paradigm_id"] == "pd-9c11ab02"
    assert "/api/v1/paradigm/resolve" not in [c.url.path for c in calls], (
        "an explicit id needs no lookup, and a lookup could only disagree with it"
    )


def test_fulltext_without_a_paradigm_id_keeps_resolving_the_domain(monkeypatch, calls):
    install(
        monkeypatch,
        backend(
            catalog=catalog_of(ODN_TOPOLOGY),
            resolve=httpx.Response(200, json={"bound": True, "paradigmId": "pd-3f2a1b7c"}),
        ),
        calls,
    )

    mcp_client.get_segment_fulltext(
        FullTextInput(domain="odn", refs=[SegmentRef(type="raw_segment", id="seg-1")])
    )

    posts = [c for c in calls if c.url.path == "/api/v1/segments/fulltext"]
    assert json.loads(posts[0].content)["paradigm_id"] == "pd-3f2a1b7c"
