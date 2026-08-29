"""Naming a paradigm explicitly: resolution, its four failure modes, and the advisory catalog.

Uses ``httpx.MockTransport`` like the other suites, so the asserted URLs and payloads are the ones
the backend would really see.

批次8 R8：成功响应是纯 EvidenceResponse（无 _retrieval）；``available_paradigms``
提示只出现在**错误**响应上（Agent 有东西可以纠正方向，成功响应保持 §13.3 硬约束）。

The theme running through every failure case: an explicit selection is never quietly served by
another engine. The caller asserted a choice, and answering from something else looks exactly
like having honoured it.
"""

from __future__ import annotations

import httpx
import pytest

from mcp_server import client as mcp_client
from mcp_server.identity import Identity
from mcp_server.schemas import SearchInput

def ident() -> Identity:
    return Identity(username="alice", user_id="u-1", open_kbs=({"id": "kb-1", "name": "基站手册库"},))


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

EVIDENCE = {
    "evidenceResponse": {
        "query": "光路不通",
        "evidence": [{"ref": "ev_1", "type": "prose", "content": "…",
                      "source": {"document_ref": "doc_1"}, "truncated": False}],
        "has_more": False,
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


#: 阶段 A 后 unnamed 搜索必须有可用范式（无 legacy 回落）——默认给个 bound resolve。
_BOUND_RESOLVE = {
    "domain": "odn", "bound": True, "paradigmId": "pd-abc",
    "name": "odn-production", "version": 3, "source": "domain",
}


def backend(*, catalog, paradigm=None, resolve=None):
    """Handler over the endpoints this suite touches."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == CATALOG_PATH:
            return catalog(request) if callable(catalog) else catalog
        if path == "/api/v1/paradigm/resolve":
            return resolve or httpx.Response(200, json=_BOUND_RESOLVE)
        if path.startswith("/api/v1/paradigm/") and path.endswith("/search"):
            return paradigm or httpx.Response(200, json=EVIDENCE)
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

    out = mcp_client.search_knowledge(q(paradigm="ODN 参数表查询"), ident(), [])

    assert executed_paradigm(calls) == "/api/v1/paradigm/pd-9c11ab02/search"
    assert out == EVIDENCE["evidenceResponse"]


def test_selects_by_id(monkeypatch, calls):
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY, ODN_TABLES)), calls)

    mcp_client.search_knowledge(q(paradigm="pd-3f2a1b7c"), ident(), [])

    assert executed_paradigm(calls) == "/api/v1/paradigm/pd-3f2a1b7c/search"


@pytest.mark.parametrize("named", ["  ODN 参数表查询  ", "odn 参数表查询", "PD-9C11AB02"])
def test_matching_tolerates_copy_paste(monkeypatch, calls, named):
    """Agents copy these strings out of a previous answer; whitespace and case ride along."""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY, ODN_TABLES)), calls)

    mcp_client.search_knowledge(q(paradigm=named), ident(), [])

    assert executed_paradigm(calls) == "/api/v1/paradigm/pd-9c11ab02/search"


def test_naming_a_paradigm_skips_the_domain_default_lookup(monkeypatch, calls):
    """The tool IS the choice; re-resolving could only disagree with it."""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY)), calls)

    mcp_client.search_knowledge(q(paradigm="ODN 拓扑排障"), ident(), [])

    assert "/api/v1/paradigm/resolve" not in [c.url.path for c in calls]


# ── the four failure modes ───────────────────────────────────────────────


def test_unknown_name_is_rejected_not_silently_redirected(monkeypatch, calls):
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY)), calls)

    out = mcp_client.search_knowledge(q(paradigm="不存在的范式"), ident(), [])

    assert out["error"] == "unknown_paradigm"
    assert "ODN 拓扑排障" in out["message"], "the agent needs the valid options to self-correct"
    assert executed_paradigm(calls) is None
    assert "/api/v1/search" not in [c.url.path for c in calls], "must not fall back to legacy"


def test_a_freshly_published_paradigm_forces_a_refresh_before_being_declared_unknown(
    monkeypatch, calls
):
    """Publish-then-immediately-use must work, whatever the cache happens to hold."""
    responses = [catalog_of(ODN_TOPOLOGY), catalog_of(ODN_TOPOLOGY, ODN_TABLES)]

    def catalog(_request):
        return responses.pop(0) if len(responses) > 1 else responses[0]

    install(monkeypatch, backend(catalog=catalog), calls)

    out = mcp_client.search_knowledge(q(paradigm="ODN 参数表查询"), ident(), [])

    assert [c.url.path for c in calls].count(CATALOG_PATH) >= 2, "stale miss must re-fetch"
    assert executed_paradigm(calls) == "/api/v1/paradigm/pd-9c11ab02/search"
    assert "error" not in out


def test_catalog_down_passes_an_id_through_but_rejects_a_name(monkeypatch, calls):
    install(monkeypatch, backend(catalog=httpx.Response(503, text="down")), calls)

    by_id = mcp_client.search_knowledge(q(paradigm="pd-3f2a1b7c"), ident(), [])
    assert executed_paradigm(calls) == "/api/v1/paradigm/pd-3f2a1b7c/search"
    assert "error" not in by_id

    calls.clear()
    by_name = mcp_client.search_knowledge(q(paradigm="ODN 拓扑排障"), ident(), [])
    assert by_name["error"] == "catalog_unavailable", (
        "a name cannot be resolved without the catalog, and 'unknown' would be a lie"
    )
    assert executed_paradigm(calls) is None
    assert "/api/v1/search" not in [c.url.path for c in calls]


# ── the advisory list (errors only — success stays pure EvidenceResponse) ──


def test_no_paradigm_configured_carries_the_available_list(monkeypatch, calls):
    """阶段 A：无任何范式绑定的域，第一次 unnamed 调用就报 no_paradigm_configured。
    错误响应上仍附可用清单——agent 有东西可以纠正方向。"""
    install(
        monkeypatch,
        backend(
            catalog=catalog_of(ODN_TOPOLOGY),
            resolve=httpx.Response(200, json={"domain": "odn", "bound": False}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert out["error"] == "no_paradigm_configured"
    assert [e["name"] for e in out["available_paradigms"]] == ["ODN 拓扑排障"]


def test_the_list_is_no_longer_domain_filtered(monkeypatch, calls):
    """批次6：域绑定退役——范式跨域通用，清单全量列出。"""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY, CCN_ONLY, ANY_DOMAIN)), calls)

    out = mcp_client.search_knowledge(q(domain="odn"), ident(), [])

    assert "error" not in out
    # 成功响应纯 EvidenceResponse——清单只在错误上可见（§13.3）
    assert "available_paradigms" not in out

    miss = mcp_client.search_knowledge(q(domain="odn", paradigm="不存在"), ident(), [])
    assert [e["name"] for e in miss["available_paradigms"]] == [
        "ODN 拓扑排障",
        "核心网配置",
        "通用检索",
    ]


def test_an_unreachable_catalog_omits_the_field_rather_than_reporting_none(monkeypatch, calls):
    """Absent and empty must not look alike: empty would read as 'there are no paradigms'."""
    install(
        monkeypatch,
        backend(
            catalog=httpx.Response(503, text="down"),
            resolve=httpx.Response(200, json={"domain": "odn", "bound": False}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert out["error"] == "no_paradigm_configured"
    assert "available_paradigms" not in out


UNBOUND = httpx.Response(200, json={"domain": "odn", "bound": False})


def test_the_catalog_is_cached_across_calls(monkeypatch, calls):
    """批次8 R8：成功响应不再拉 catalog（无 _retrieval 提示）——错误路径才用它，仍被缓存。"""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY), resolve=UNBOUND), calls)

    mcp_client.search_knowledge(q(), ident(), [])
    mcp_client.search_knowledge(q(), ident(), [])

    # 成功路径不触发；两次 no_paradigm_configured 共享一次 catalog 拉取
    assert [c.url.path for c in calls].count(CATALOG_PATH) == 1


def test_an_expired_cache_refetches(monkeypatch, calls):
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY), resolve=UNBOUND), calls)
    monkeypatch.setattr(mcp_client, "CATALOG_TTL", 0.0)

    mcp_client.search_knowledge(q(), ident(), [])
    mcp_client.search_knowledge(q(), ident(), [])

    assert [c.url.path for c in calls].count(CATALOG_PATH) == 2


# ── self-review follow-ups ───────────────────────────────────────────────


def test_catalog_unavailable_does_not_pay_the_timeout_twice(monkeypatch, calls):
    """The hint we would attach is the very thing that just failed to load.

    Asking again doubles the wall-clock an agent waits for an error the first attempt already
    determined — with MCP_CATALOG_TIMEOUT at 5s that is 10 seconds of nothing.
    """
    install(monkeypatch, backend(catalog=httpx.Response(503, text="down")), calls)

    out = mcp_client.search_knowledge(q(paradigm="某个中文范式名"), ident(), [])

    assert out["error"] == "catalog_unavailable"
    assert [c.url.path for c in calls].count(CATALOG_PATH) == 1
    assert "available_paradigms" not in out


def test_unknown_paradigm_still_lists_the_options(monkeypatch, calls):
    """The opposite case: the catalog is fine, so the error must carry what IS valid."""
    install(monkeypatch, backend(catalog=catalog_of(ODN_TOPOLOGY)), calls)

    out = mcp_client.search_knowledge(q(paradigm="不存在"), ident(), [])

    assert out["error"] == "unknown_paradigm"
    assert [e["name"] for e in out["available_paradigms"]] == ["ODN 拓扑排障"]


def test_malformed_catalog_entries_are_dropped_not_fatal(monkeypatch, calls):
    """A hint must never be the reason a search fails — including on a malformed response."""
    install(
        monkeypatch,
        backend(
            catalog=httpx.Response(200, json={"paradigms": [
                {"name": "没有 id 的条目"},
                {"id": None, "name": "id 是 null"},
                "不是对象",
                ODN_TOPOLOGY,
            ]}),
        ),
        calls,
    )

    out = mcp_client.search_knowledge(q(), ident(), [])

    assert "error" not in out
