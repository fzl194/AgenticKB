"""2026-08-31 工具族收敛（两轮 9→7→3）的纯逻辑契约。

覆盖：
- validate_domain 三态（不传=钥匙域 / 相等通过 / 不等报错）——单域钥匙语义
- get_knowledge 分流矩阵：kb_tree / documents / capabilities / evidence_content /
  document_content / table_rows / navigation 七种 view，与参数互斥的显式报错
"""
from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from mcp_server.identity import Identity, IdentityError, validate_domain
from mcp_server import server


def ident_of(kbs: list[tuple[str, str, str]], key_domain: str = "cloud_core_network") -> Identity:
    """kbs: [(id, name, domain)]；key_domain = 钥匙绑定的知识域（批次2 单域钥匙）。"""
    return Identity(
        username="alice",
        user_id="u-1",
        key_id="key-1",
        key_domain=key_domain,
        open_kbs=tuple(
            {"id": i, "name": n, "domain": d} for i, n, d in kbs
        ),
    )


SINGLE = ident_of(
    [("kb-1", "网络手册库", "cloud_core_network")],
    key_domain="cloud_core_network",
)


# ── validate_domain（M3：domain 只是校验参数） ───────────────────────────


def test_absent_domain_means_key_domain() -> None:
    assert validate_domain(SINGLE, None) == "cloud_core_network"
    assert validate_domain(SINGLE, "") == "cloud_core_network"
    assert validate_domain(SINGLE, "  ") == "cloud_core_network"


def test_matching_explicit_domain_passes_through() -> None:
    assert validate_domain(SINGLE, "cloud_core_network") == "cloud_core_network"
    assert validate_domain(SINGLE, "  cloud_core_network ") == "cloud_core_network"


def test_mismatched_domain_is_rejected_with_both_names() -> None:
    with pytest.raises(
        IdentityError,
        match="绑定知识域 'cloud_core_network'.*收到 'civil_engineering'",
    ):
        validate_domain(SINGLE, "civil_engineering")


# ── get_knowledge 分流矩阵 ───────────────────────────────────────────────


def _patch_backend(monkeypatch, ident=SINGLE):
    """替身 identity 与五个 backend 通道；返回调用记录。"""
    calls: list[tuple] = []
    monkeypatch.setattr(server, "_identity", lambda: ident)

    def note(kind, ret=None):
        def _fn(*args, **kwargs):
            # 关键字实参按传入顺序追加（57 号文件搜索过滤走 kwargs）
            calls.append((kind,) + args + tuple(kwargs.values()))
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
        lambda username, key_id: {"knowledge_bases": [
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
    assert calls == [("docs", "alice", "key-1", "kb-1", 10, 5, None, None, None)]


# ── 57 号：文件搜索（file_query / directory_prefix / status）─────────────


def test_file_query_without_kb_name_searches_across_open_kbs(monkeypatch) -> None:
    """只传 file_query（无 kb_name/ref）= 跨全部开放库搜文件（view=file_results）。"""
    calls = _patch_backend(monkeypatch)
    out = server.get_knowledge(file_query="手册", status="failed", limit=20)
    assert out["view"] == "file_results"
    assert calls == [
        ("docs", "alice", "key-1", None, 20, 0,
         "手册", None, "failed"),
    ]


def test_kb_name_with_file_query_filters_within_kb(monkeypatch) -> None:
    """kb_name + file_query = 该库内搜文件（view=documents，搜索模式）。"""
    calls = _patch_backend(monkeypatch)
    out = server.get_knowledge(
        kb_name="网络手册库", file_query="告警", directory_prefix="产品文档", limit=5)
    assert out["view"] == "documents"
    assert calls == [
        ("docs", "alice", "key-1", "kb-1", 5, 0,
         "告警", "产品文档", None),
    ]


def test_ref_rejects_file_query_like_kb_name(monkeypatch) -> None:
    """ref 与 file_query 互斥：ref=深入引用，file_query=找文件。"""
    _patch_backend(monkeypatch)
    with pytest.raises(ToolError, match="ref 与 kb_name/file_query 不能同时传"):
        server.get_knowledge(ref="ev_X", file_query="手册")


def test_bare_status_without_file_query_still_browses_tree(monkeypatch) -> None:
    """status/directory_prefix 只在 file_query/kb_name 场景有意义；裸传不改变分流。"""
    _patch_backend(monkeypatch)
    assert server.get_knowledge(status="failed")["view"] == "kb_tree"


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
    with pytest.raises(ToolError, match="ref 与 kb_name/file_query 不能同时传"):
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


# ── upload_document 两步直传（2026-09-11 改造：无 base64） ────────────────


def test_upload_document_returns_direct_upload_urls(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    monkeypatch.setattr(
        server.backend, "begin_upload",
        lambda username, key_id, kb_id, filename, **kwargs: {
            "ticket": f"up_{filename}", "max_bytes": 52_428_800,
            "expires_in": 600,
        },
    )
    monkeypatch.setattr(
        server, "get_http_headers",
        lambda include=None: {"host": "kb.example.com:9000"},
    )

    out = server.upload_document(kb_name="网络手册库",
                                 filenames=["手册.pdf", "notes.md"])

    assert [u["filename"] for u in out["uploads"]] == ["手册.pdf", "notes.md"]
    assert out["uploads"][0]["upload_url"] ==         "http://kb.example.com:9000/upload/up_手册.pdf"
    assert out["uploads"][0]["method"] == "PUT"
    assert out["uploads"][0]["max_bytes"] == 52_428_800
    assert out["uploads"][0]["expires_in"] == 600
    assert "不要 base64" in out["usage"]
    assert "zip" in out["batch_tip"]


def test_upload_document_respects_forwarded_proto(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    monkeypatch.setattr(
        server.backend, "begin_upload",
        lambda *a, **k: {"ticket": "up_t", "max_bytes": 1, "expires_in": 600},
    )
    monkeypatch.setattr(
        server, "get_http_headers",
        lambda include=None: {"host": "kb.example.com",
                              "x-forwarded-proto": "https"},
    )
    out = server.upload_document(kb_name="网络手册库", filenames=["a.md"])
    assert out["uploads"][0]["upload_url"].startswith("https://kb.example.com/upload/")


def test_upload_document_rejects_path_like_filename(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    with pytest.raises(ToolError, match="filename 非法"):
        server.upload_document(kb_name="网络手册库", filenames=["../evil.md"])
    with pytest.raises(ToolError, match="filename 非法"):
        server.upload_document(kb_name="网络手册库", filenames=["a/b.md"])
    with pytest.raises(ToolError, match="不能为空"):
        server.upload_document(kb_name="网络手册库", filenames=[])


# ── 57号D5：upload_document 替换模式（doc_ ref + 版本暗号）─────────────────


def _patch_for_replace(monkeypatch, *, live_revision=3, document_id="doc-internal-1"):
    calls: list[tuple] = []
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    monkeypatch.setattr(
        server.backend, "get_document",
        lambda *a, **k: (calls.append(("resolve",) + a),
                         {"source": {"document_id": document_id,
                                     "content_revision": live_revision,
                                     "file_name": "手册.pdf"},
                          "segments": []})[1])
    monkeypatch.setattr(
        server.backend, "begin_upload",
        lambda *a, **k: (calls.append(("begin",) + a + tuple(k.values())),
                         {"ticket": "up_r", "max_bytes": 1, "expires_in": 600})[1])
    monkeypatch.setattr(
        server, "get_http_headers", lambda include=None: {"host": "kb.example.com"})
    return calls


def test_upload_document_replace_defaults_to_live_revision(monkeypatch) -> None:
    calls = _patch_for_replace(monkeypatch, live_revision=7)
    out = server.upload_document(
        kb_name="网络手册库", filenames=["手册.pdf"], replace_document_ref="doc_ABC")
    assert out["uploads"][0]["mode"] == "replace"
    assert out["uploads"][0]["document_ref"] == "doc_ABC"
    begin = [c for c in calls if c[0] == "begin"][0]
    assert "doc-internal-1" in begin      # 内部 id 只到 mining，不进返回
    assert 7 in begin                     # 暗号缺省=解析到的当前版本
    assert "doc-internal-1" not in str(out)


def test_upload_document_replace_explicit_revision_passthrough(monkeypatch) -> None:
    calls = _patch_for_replace(monkeypatch, live_revision=5)
    server.upload_document(
        kb_name="网络手册库", filenames=["手册.pdf"],
        replace_document_ref="doc_ABC", expected_revision=4)
    begin = [c for c in calls if c[0] == "begin"][0]
    assert 4 in begin and 5 not in begin


def test_upload_document_replace_rejects_bad_shapes(monkeypatch) -> None:
    _patch_for_replace(monkeypatch)
    with pytest.raises(ToolError, match="doc_ 前缀"):
        server.upload_document(kb_name="网络手册库", filenames=["a.pdf"],
                               replace_document_ref="st_X")
    with pytest.raises(ToolError, match="一次只能替换一个"):
        server.upload_document(kb_name="网络手册库", filenames=["a.pdf", "b.pdf"],
                               replace_document_ref="doc_X")
    with pytest.raises(ToolError, match="非负整数"):
        server.upload_document(kb_name="网络手册库", filenames=["a.pdf"],
                               replace_document_ref="doc_X", expected_revision=-1)


def test_upload_document_replace_unresolvable_ref_guides_retry(monkeypatch) -> None:
    _patch_for_replace(monkeypatch, document_id="")
    with pytest.raises(ToolError, match="无法解析该文档引用"):
        server.upload_document(kb_name="网络手册库", filenames=["a.pdf"],
                               replace_document_ref="doc_GONE")


def test_doc_view_strips_internal_document_id(monkeypatch) -> None:
    """get_knowledge doc_ 分支剥 document_id（Agent 只见 ref+暗号），revision 保留。"""
    calls = _patch_backend(monkeypatch)
    monkeypatch.setattr(
        server.backend, "get_document",
        lambda *a, **k: {"source": {"document_id": "d-9", "content_revision": 2,
                                    "file_name": "x.pdf"},
                         "segments": []})
    out = server.get_knowledge(ref="doc_X")
    assert out["view"] == "document_content"
    assert "document_id" not in out["source"]
    assert out["source"]["content_revision"] == 2


def test_upload_document_limits_filename_count_and_length(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    with pytest.raises(ToolError, match="最多"):
        server.upload_document(
            kb_name="网络手册库",
            filenames=[f"{index}.md" for index in range(101)],
        )
    with pytest.raises(ToolError, match="过长"):
        server.upload_document(
            kb_name="网络手册库",
            filenames=["x" * 256],
        )


def test_upload_document_rejects_kb_not_open(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    with pytest.raises(ToolError, match="未开放或不存在"):
        server.upload_document(kb_name="别的库", filenames=["a.md"])


@pytest.mark.asyncio
async def test_direct_upload_route_needs_no_auth_header(monkeypatch) -> None:
    """票据即凭证：无任何认证头的 PUT 直达 backend（密钥只在 MCP 客户端）。"""
    from httpx import ASGITransport, AsyncClient

    seen: dict = {}

    async def fake_put(ticket, stream):
        body = b""
        async for chunk in stream:
            body += chunk
        seen["ticket"], seen["body"] = ticket, body
        return 200, {"document_id": "d1"}

    monkeypatch.setattr(server.backend, "put_upload_direct", fake_put)

    app = server.mcp.http_app()
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as client:
        resp = await client.put("/upload/up_bare", content=b"raw")

    assert resp.status_code == 200
    assert resp.json()["document_id"] == "d1"
    assert seen == {"ticket": "up_bare", "body": b"raw"}


@pytest.mark.asyncio
async def test_direct_upload_route_streams_to_backend(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    async def fake_put(ticket, stream):
        body = b""
        async for chunk in stream:
            body += chunk
        return 200, {"document_id": "d1", "auto_mined": True,
                     "run_id": "r1", "message": "ok"}

    monkeypatch.setattr(server.backend, "put_upload_direct", fake_put)

    app = server.mcp.http_app()
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as client:
        resp = await client.put("/upload/up_good", content=b"raw-bytes")

    assert resp.status_code == 200
    assert resp.json()["document_id"] == "d1"
    assert resp.json()["auto_mined"] is True


@pytest.mark.asyncio
async def test_direct_upload_route_maps_backend_status(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    async def fake_put(ticket, stream):
        return 413, {"detail": "file too large (>50MB)"}

    monkeypatch.setattr(server.backend, "put_upload_direct", fake_put)

    app = server.mcp.http_app()
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as client:
        resp = await client.put("/upload/up_big", content=b"x")

    assert resp.status_code == 413
    assert "50MB" in resp.json()["detail"]
