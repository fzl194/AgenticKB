"""2026-08-31 工具族收敛（两轮 9→7→3）的纯逻辑契约。

覆盖：
- Identity.resolve_domain 三态（唯一域自动 / 多域带清单报错 / 显式优先且校验）
- get_knowledge 分流矩阵：kb_tree / documents / capabilities / evidence_content /
  document_content / table_rows / navigation 七种 view，与参数互斥的显式报错
"""
from __future__ import annotations

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


# ── get_knowledge 分流矩阵 ───────────────────────────────────────────────


def _patch_backend(monkeypatch, ident=SINGLE):
    """替身 identity 与五个 backend 通道；返回调用记录。"""
    calls: list[tuple] = []
    monkeypatch.setattr(server, "_identity", lambda: ident)

    def note(kind, ret=None):
        def _fn(*args):
            calls.append((kind,) + args)
            return ret if ret is not None else {}
        return _fn

    monkeypatch.setattr(server.backend, "get_evidence", note("evidence", {"content": "x"}))
    monkeypatch.setattr(server.backend, "get_document", note("document", {"segments": []}))
    monkeypatch.setattr(server.backend, "inspect_knowledge", note("inspect", {"capabilities": {}}))
    monkeypatch.setattr(server.backend, "navigate_structure", note("navigate", {"nodes": []}))
    monkeypatch.setattr(
        server.backend, "query_structured_asset", note("query", {"rows": []}))
    monkeypatch.setattr(
        server.backend, "list_documents", note("docs", {"documents": []}))
    monkeypatch.setattr(
        server.backend, "list_knowledge_bases",
        lambda username: {"knowledge_bases": [
            {"id": "kb-1", "name": "网络手册库", "domain": "cloud_core_network"}]})
    return calls


def test_bare_call_returns_kb_tree(monkeypatch) -> None:
    _patch_backend(monkeypatch)
    out = server.get_knowledge()
    assert out["view"] == "kb_tree"
    assert out["default_domain"] == "cloud_core_network"
    assert out["domains"][0]["knowledge_bases"] == [{"name": "网络手册库"}]


def test_kb_name_lists_documents(monkeypatch) -> None:
    calls = _patch_backend(monkeypatch)
    out = server.get_knowledge(kb_name="网络手册库", limit=10, offset=5)
    assert out["view"] == "documents"
    assert calls == [("docs", "alice", "kb-1", 10, 5)]


def test_bare_ref_semantics_per_ref_type(monkeypatch) -> None:
    """ev_/doc_ 是内容引用（只传 ref 直接给内容）；st_ 是结构引用（给能力报告）。"""
    calls = _patch_backend(monkeypatch)
    assert server.get_knowledge(ref="ev_X")["view"] == "evidence_content"
    assert server.get_knowledge(ref="doc_X")["view"] == "document_content"
    assert server.get_knowledge(ref="st_X")["view"] == "capabilities"
    assert [c[0] for c in calls] == ["evidence", "document", "inspect"]


def test_ev_ref_with_mode_returns_evidence_content(monkeypatch) -> None:
    calls = _patch_backend(monkeypatch)
    out = server.get_knowledge(ref="ev_ABC", mode="whole_document")
    assert out["view"] == "evidence_content"
    assert out["content"] == "x"
    assert calls == [("evidence", "alice", ["kb-1"], "cloud_core_network", "ev_ABC", "whole_document")]


def test_doc_ref_paginates(monkeypatch) -> None:
    calls = _patch_backend(monkeypatch)
    out = server.get_knowledge(ref="doc_ABC", limit=2, cursor="c1")
    assert out["view"] == "document_content"
    assert calls == [("document", "alice", ["kb-1"], "cloud_core_network", "doc_ABC", 2, "c1")]


def test_st_ref_with_query_runs_structured_query(monkeypatch) -> None:
    calls = _patch_backend(monkeypatch)
    out = server.get_knowledge(ref="st_T", query={"select": ["列A"]})
    assert out["view"] == "table_rows"
    agg = server.get_knowledge(ref="st_T", query={"aggregate": {"op": "avg", "field": "列A"}})
    assert agg["view"] == "aggregate"
    assert calls[0] == ("query", "alice", ["kb-1"], "cloud_core_network", "st_T", {"select": ["列A"]})


def test_st_ref_with_relation_navigates(monkeypatch) -> None:
    calls = _patch_backend(monkeypatch)
    out = server.get_knowledge(ref="st_N", relation="children", depth=1, limit=20)
    assert out["view"] == "navigation"
    assert calls == [("navigate", "alice", ["kb-1"], "cloud_core_network",
                      "st_N", "children", 1, 20, None)]


def test_parameter_conflicts_are_explicit_errors(monkeypatch) -> None:
    _patch_backend(monkeypatch)
    # ref 与 kb_name 互斥
    with pytest.raises(ToolError, match="ref 与 kb_name 不能同时传"):
        server.get_knowledge(ref="st_X", kb_name="网络手册库")
    # ev_ 不支持导航/查表 → 指向 structure_ref
    with pytest.raises(ToolError, match="structure_ref"):
        server.get_knowledge(ref="ev_X", relation="children")
    with pytest.raises(ToolError, match="structure_ref"):
        server.get_knowledge(ref="ev_X", query={"select": []})
    # doc_ 同理
    with pytest.raises(ToolError, match="structure_ref"):
        server.get_knowledge(ref="doc_X", relation="children")
    # query 与 relation 互斥
    with pytest.raises(ToolError, match="query 与 relation 不能同时传"):
        server.get_knowledge(ref="st_X", relation="children", query={"select": []})
    # mode 只对 ev_ 有效（不静默忽略）
    with pytest.raises(ToolError, match="mode.*只用于 ev_"):
        server.get_knowledge(ref="st_X", mode="whole_document")
    with pytest.raises(ToolError, match="mode.*只用于 ev_"):
        server.get_knowledge(ref="st_X", relation="children", mode="exact")


def test_tool_registry_is_the_three_piece_family() -> None:
    from mcp_server.identity import TOOL_NAMES
    assert TOOL_NAMES == frozenset({
        "search_knowledge", "get_knowledge", "upload_document",
    })
