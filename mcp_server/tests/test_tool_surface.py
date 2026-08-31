"""2026-08-31 工具族收敛（9→7）与 domain 免传的纯逻辑契约。

覆盖：
- Identity.resolve_domain 三态（唯一域自动 / 多域带清单报错 / 显式优先且校验）
- get_content 按 ref 前缀分流（ev_ 证据展开 / doc_ 整文分页 / st_ 拒绝）
- browse_knowledge 层级（顶层按域分组 / 传 kb_name 列文档 / domain 过滤）
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from mcp_server.identity import Identity, IdentityError
from mcp_server import server


def ident_of(kbs: list[tuple[str, str, str]], domains: tuple[str, ...] = ()) -> Identity:
    """kbs: [(id, name, domain)]；domains 显式给出（正常来自 verify 响应推导）。"""
    return Identity(
        username="alice",
        user_id="u-1",
        open_kbs=tuple(
            {"id": i, "name": n, "domain": d} for i, n, d in kbs
        ),
        domains=domains,
    )


SINGLE = ident_of(
    [("kb-1", "网络手册库", "cloud_core_network")],
    domains=("cloud_core_network",),
)
MULTI = ident_of(
    [("kb-1", "网络手册库", "cloud_core_network"),
     ("kb-2", "通用库", "generic")],
    domains=("cloud_core_network", "generic"),
)


# ── resolve_domain ───────────────────────────────────────────────────────


def test_single_domain_defaults_automatically() -> None:
    assert SINGLE.resolve_domain(None) == "cloud_core_network"
    assert SINGLE.resolve_domain("") == "cloud_core_network"
    assert SINGLE.resolve_domain("  ") == "cloud_core_network"


def test_explicit_domain_wins_and_is_validated() -> None:
    assert SINGLE.resolve_domain("cloud_core_network") == "cloud_core_network"
    with pytest.raises(IdentityError, match="不在你的开放知识库覆盖范围.*cloud_core_network"):
        MULTI.resolve_domain("civil_engineering")


def test_multi_domain_without_explicit_lists_the_choices() -> None:
    with pytest.raises(
        IdentityError, match="多个知识域：cloud_core_network、generic。请从中选择"
    ):
        MULTI.resolve_domain(None)


def test_no_domain_info_requires_explicit() -> None:
    bare = ident_of([("kb-1", "库", "whatever")], domains=())
    with pytest.raises(IdentityError, match="无法确定默认知识域"):
        bare.resolve_domain(None)
    # 显式传入时无清单可校验 → 放行（旧 mining 混布窗口的兼容语义）
    assert bare.resolve_domain("odn") == "odn"


# ── get_content 分流 ─────────────────────────────────────────────────────


def test_get_content_routes_by_ref_prefix(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    monkeypatch.setattr(
        server.backend, "get_evidence",
        lambda username, kb_ids, domain, ref, mode=None:
            calls.append(("ev", domain, ref, mode)) or {"content": "x"},
    )
    monkeypatch.setattr(
        server.backend, "get_document",
        lambda username, kb_ids, domain, ref, limit=None, cursor=None:
            calls.append(("doc", domain, ref, limit, cursor)) or {"outline": []},
    )

    out = server.get_content("ev_ABC", None)
    assert out == {"content": "x"}
    out = server.get_content("doc_XYZ", None, limit=10, cursor="c1")
    assert out == {"outline": []}
    assert calls == [
        ("ev", "cloud_core_network", "ev_ABC", None),
        ("doc", "cloud_core_network", "doc_XYZ", 10, "c1"),
    ]


def test_get_content_rejects_structure_ref(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    with pytest.raises(ToolError, match="navigate_structure"):
        server.get_content("st_ABC", None)


# ── browse_knowledge 层级 ────────────────────────────────────────────────


def _patch_listing(monkeypatch, kbs: list[dict]) -> None:
    monkeypatch.setattr(server, "_identity", lambda: MULTI)
    monkeypatch.setattr(
        server.backend, "list_knowledge_bases",
        lambda username: {"knowledge_bases": kbs},
    )


def test_browse_top_level_groups_by_domain(monkeypatch) -> None:
    _patch_listing(monkeypatch, [
        {"id": "kb-1", "name": "网络手册库", "description": "核心网手册",
         "domain": "cloud_core_network"},
        {"id": "kb-2", "name": "通用库", "domain": "generic"},
    ])
    out = server.browse_knowledge()
    assert [g["domain"] for g in out["domains"]] == [
        "cloud_core_network", "generic"]
    assert out["domains"][0]["knowledge_bases"] == [
        {"name": "网络手册库", "description": "核心网手册"}]
    # 内部 id 不暴露；default_domain 只在唯一域时给出
    assert "id" not in out["domains"][0]["knowledge_bases"][0]
    assert out["default_domain"] is None


def test_browse_domain_filter_and_unknown(monkeypatch) -> None:
    _patch_listing(monkeypatch, [
        {"id": "kb-1", "name": "网络手册库", "domain": "cloud_core_network"},
        {"id": "kb-2", "name": "通用库", "domain": "generic"},
    ])
    out = server.browse_knowledge(domain="generic")
    assert [g["domain"] for g in out["domains"]] == ["generic"]
    assert out["default_domain"] == "generic"
    with pytest.raises(ToolError, match="没有开放的知识库.*generic"):
        server.browse_knowledge(domain="odn")


def test_browse_with_kb_name_lists_documents(monkeypatch) -> None:
    seen: list[tuple] = []
    monkeypatch.setattr(server, "_identity", lambda: MULTI)
    monkeypatch.setattr(
        server.backend, "list_documents",
        lambda username, kb_id, limit, offset:
            seen.append((kb_id, limit, offset)) or {"documents": []},
    )
    out = server.browse_knowledge(kb_name="网络手册库", limit=10, offset=5)
    assert out == {"documents": []}
    assert seen == [("kb-1", 10, 5)]


def test_tool_registry_is_the_seven_piece_family() -> None:
    from mcp_server.identity import TOOL_NAMES
    assert TOOL_NAMES == frozenset({
        "search_knowledge", "get_content", "browse_knowledge",
        "inspect_knowledge", "navigate_structure",
        "query_structured_asset", "upload_document",
    })
