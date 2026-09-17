"""批次2 Task 7：mcp-tools 三端点透传 key_id + kb_tree 单域分组。

- list-kbs / list-documents / begin-upload 的 POST body 必须携带 key_id
  （与 username 同路——body 携带，X-Internal-Auth 头不动）；
- get_knowledge 顶层浏览（kb_tree）：分组只含钥匙绑定域（key_domain 单值），
  其他域的库不出现，domain 参数退化为校验参数。
"""
from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from mcp_server import server
from mcp_server import tools
from mcp_server.identity import Identity, IdentityError


def ident_of(kbs, key_domain="cloud_core_network") -> Identity:
    return Identity(
        username="alice",
        user_id="u-1",
        key_id="key-1",
        key_domain=key_domain,
        open_kbs=tuple({"id": i, "name": n, "domain": d} for i, n, d in kbs),
    )


SINGLE = ident_of([("kb-1", "网络手册库", "cloud_core_network")])


# ── tools 层：三个 POST body 携带 key_id ─────────────────────────────────


def test_list_kbs_body_carries_key_id(monkeypatch) -> None:
    seen: dict = {}

    def fake_post(path, payload, *, timeout=None):
        seen["path"], seen["payload"] = path, payload
        return {"knowledge_bases": []}

    monkeypatch.setattr(tools, "_post", fake_post)
    tools.list_knowledge_bases("alice", "key-1")
    assert seen["path"] == "/api/kb/mcp-tools/list-kbs"
    assert seen["payload"]["key_id"] == "key-1"
    assert seen["payload"]["username"] == "alice"


def test_list_documents_body_carries_key_id(monkeypatch) -> None:
    seen: dict = {}

    def fake_post(path, payload, *, timeout=None):
        seen["path"], seen["payload"] = path, payload
        return {"documents": []}

    monkeypatch.setattr(tools, "_post", fake_post)
    tools.list_documents("alice", "key-1", "kb-9", limit=5, offset=10)
    assert seen["path"] == "/api/kb/mcp-tools/list-documents"
    assert seen["payload"] == {
        "username": "alice", "key_id": "key-1", "kb_id": "kb-9",
        "limit": 5, "offset": 10,
    }


def test_begin_upload_body_carries_key_id(monkeypatch) -> None:
    seen: dict = {}

    def fake_post(path, payload, *, timeout=None):
        seen["path"], seen["payload"] = path, payload
        return {"ticket": "t", "max_bytes": 1, "expires_in": 600}

    monkeypatch.setattr(tools, "_post", fake_post)
    tools.begin_upload("alice", "key-1", "kb-9", "手册.pdf")
    assert seen["path"] == "/api/kb/mcp-tools/begin-upload"
    assert seen["payload"] == {
        "username": "alice", "key_id": "key-1",
        "kb_id": "kb-9", "filename": "手册.pdf",
    }


# ── server 层：调用点透传 identity.key_id ────────────────────────────────


def test_server_passes_key_id_to_backend(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)

    def note(kind, ret=None):
        def _fn(*args):
            calls.append((kind,) + args)
            return ret if ret is not None else {}
        return _fn

    monkeypatch.setattr(
        server.backend, "list_knowledge_bases",
        note("list-kbs", {"knowledge_bases": [
            {"id": "kb-1", "name": "网络手册库", "domain": "cloud_core_network"}]}))
    monkeypatch.setattr(
        server.backend, "list_documents", note("docs", {"documents": []}))
    monkeypatch.setattr(
        server.backend, "begin_upload",
        note("begin-upload", {"ticket": "t", "max_bytes": 1, "expires_in": 600}))
    monkeypatch.setattr(
        server, "get_http_headers",
        lambda include=None: {"host": "kb.example.com"})

    server.get_knowledge()
    server.get_knowledge(kb_name="网络手册库")
    server.upload_document(kb_name="网络手册库", filenames=["a.md"])

    assert [c[:3] for c in calls] == [
        ("list-kbs", "alice", "key-1"),
        ("docs", "alice", "key-1"),
        ("begin-upload", "alice", "key-1"),
    ]


# ── kb_tree 单域分组（钥匙域） ───────────────────────────────────────────


def _patch_listing(monkeypatch, ident, kbs):
    monkeypatch.setattr(server, "_identity", lambda: ident)
    monkeypatch.setattr(
        server.backend, "list_knowledge_bases",
        lambda username, key_id: {"knowledge_bases": kbs})


def test_kb_tree_only_groups_key_domain(monkeypatch) -> None:
    """listing 混入其他域的库 → 只保留钥匙域分组（单域钥匙语义）。"""
    ident = ident_of([
        ("kb-1", "网络手册库", "cloud_core_network"),
        ("kb-2", "桥梁手册库", "civil_engineering"),
    ])
    _patch_listing(monkeypatch, ident, [
        {"id": "kb-1", "name": "网络手册库", "domain": "cloud_core_network"},
        {"id": "kb-2", "name": "桥梁手册库", "domain": "civil_engineering"},
    ])
    out = server.get_knowledge()
    assert [g["domain"] for g in out["domains"]] == ["cloud_core_network"]
    assert out["domains"][0]["knowledge_bases"] == [{"name": "网络手册库"}]
    assert out["default_domain"] == "cloud_core_network"


def test_kb_tree_explicit_matching_domain_passes(monkeypatch) -> None:
    _patch_listing(monkeypatch, SINGLE, [
        {"id": "kb-1", "name": "网络手册库", "domain": "cloud_core_network"}])
    out = server.get_knowledge(domain="cloud_core_network")
    assert out["view"] == "kb_tree"
    assert out["default_domain"] == "cloud_core_network"


def test_kb_tree_foreign_domain_is_rejected(monkeypatch) -> None:
    """domain 传了非钥匙域 → validate_domain 报错（校验参数，不是过滤参数）。"""
    _patch_listing(monkeypatch, SINGLE, [])
    with pytest.raises(ToolError, match="绑定知识域"):
        server.get_knowledge(domain="civil_engineering")


def test_kb_tree_empty_key_domain_is_server_error(monkeypatch) -> None:
    _patch_listing(monkeypatch, ident_of([], key_domain=""), [])
    with pytest.raises(ToolError, match="key_domain"):
        server.get_knowledge()
