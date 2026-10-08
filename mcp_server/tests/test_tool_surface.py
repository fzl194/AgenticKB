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


# ── 58号§3：目录逐层浏览（browse_directory）─────────────────────────────


def test_browse_root_returns_directory_view(monkeypatch) -> None:
    calls: list[tuple] = []

    def note(*args, **kwargs):
        calls.append(args + tuple(kwargs.values()))
        return {"view": "directory", "child_directories": [], "documents": []}

    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    monkeypatch.setattr(server.backend, "browse_directory", note)
    out = server.get_knowledge(kb_name="网络手册库", browse_directory="")
    assert out["view"] == "directory"
    assert calls[0][:4] == ("alice", "key-1", "kb-1", "")  # 第5参=limit 默认 50


def test_browse_subdirectory_passes_path(monkeypatch) -> None:
    calls: list[tuple] = []

    def note(*args, **kwargs):
        calls.append(args + tuple(kwargs.values()))
        return {"view": "directory", "child_directories": [], "documents": []}

    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    monkeypatch.setattr(server.backend, "browse_directory", note)
    server.get_knowledge(kb_name="网络手册库", browse_directory="产品文档/手册",
                         limit=10, offset=5)
    assert calls[0][3] == "产品文档/手册"


def test_browse_requires_kb_name_and_exclusivity(monkeypatch) -> None:
    _patch_backend(monkeypatch)
    with pytest.raises(ToolError, match="kb_name"):
        server.get_knowledge(browse_directory="")
    with pytest.raises(ToolError, match="browse_directory"):
        server.get_knowledge(kb_name="网络手册库", browse_directory="",
                             file_query="手册")
    with pytest.raises(ToolError, match="browse_directory"):
        server.get_knowledge(kb_name="网络手册库", browse_directory="",
                             directory_prefix="产品文档")
    with pytest.raises(ToolError, match="browse_directory"):
        server.get_knowledge(kb_name="网络手册库", browse_directory="",
                             status="failed")
    with pytest.raises(ToolError, match="ref"):
        server.get_knowledge(ref="ev_X", browse_directory="")


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


def test_bare_filters_without_search_context_rejected(monkeypatch) -> None:
    """codex P3：status/directory_prefix 脱离搜索上下文（无 kb_name/file_query）
    = 显式报错，不静默忽略（kb_name+status 的库内清单场景不受影响）。"""
    _patch_backend(monkeypatch)
    with pytest.raises(ToolError, match="只在 file_query"):
        server.get_knowledge(status="failed")
    with pytest.raises(ToolError, match="只在 file_query"):
        server.get_knowledge(directory_prefix="产品文档")


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


def test_st_ref_with_query_merges_top_level_cursor(monkeypatch) -> None:
    """内网 1.1.14 实测修复：serving 契约=cursor 在 query 字典内，顶层 cursor
    参数必须合并进转发 payload——否则表格翻页永远停第一页、游标反复重发。"""
    calls = _patch_backend(monkeypatch)
    server.get_knowledge(ref="st_T", query={"select": ["列A"], "limit": 3},
                         cursor="bzoz")
    assert calls[0][-1] == {"select": ["列A"], "limit": 3, "cursor": "bzoz"}
    # 原始 dict 不被原地污染（不可变合并）
    q = {"select": ["列A"], "limit": 3}
    server.get_knowledge(ref="st_T", query=q, cursor="bzoz")
    assert q == {"select": ["列A"], "limit": 3}


def test_st_ref_query_cursor_conflicts_are_explicit(monkeypatch) -> None:
    _patch_backend(monkeypatch)
    with pytest.raises(ToolError, match="不要同时在 query 里塞 cursor"):
        server.get_knowledge(ref="st_T", query={"select": [], "cursor": "x"},
                             cursor="bzoz")
    with pytest.raises(ToolError, match="cursor 与 aggregate 不能同时传"):
        server.get_knowledge(
            ref="st_T", query={"aggregate": {"op": "count"}}, cursor="bzoz")


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
        "search_knowledge", "get_knowledge", "manage_files",
    })


# ── manage_files 两步直传（58号：upload/replace 双 action） ────────────────


def _patch_begin_upload(monkeypatch, calls=None):
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    monkeypatch.setattr(
        server.backend, "begin_upload",
        lambda *a, **k: (calls.append({"args": a, "kwargs": k}) if calls is not None
                         else None,
                         {"ticket": f"up_{a[3]}", "max_bytes": 52_428_800,
                          "expires_in": 600})[1])
    monkeypatch.setattr(
        server, "get_http_headers",
        lambda include=None: {"host": "kb.example.com:9000"},
    )


def test_manage_files_upload_returns_direct_upload_urls(monkeypatch) -> None:
    calls: list[dict] = []
    _patch_begin_upload(monkeypatch, calls)

    out = server.manage_files(action="upload", kb_name="网络手册库",
                              filenames=["手册.pdf", "notes.md"])

    assert [u["filename"] for u in out["uploads"]] == ["手册.pdf", "notes.md"]
    assert out["uploads"][0]["upload_url"] == "http://kb.example.com:9000/upload/up_手册.pdf"
    assert out["uploads"][0]["method"] == "PUT"
    assert out["uploads"][0]["max_bytes"] == 52_428_800
    assert out["uploads"][0]["expires_in"] == 600
    assert "不要 base64" in out["usage"]
    assert "zip" in out["batch_tip"]
    # upload 模式不携带替换参数
    assert all("document_id" not in kw["kwargs"] for kw in calls)


def test_manage_files_upload_passes_directory(monkeypatch) -> None:
    calls: list[dict] = []
    _patch_begin_upload(monkeypatch, calls)
    out = server.manage_files(action="upload", kb_name="网络手册库",
                              filenames=["手册.pdf"],
                              directory_path="产品文档/交换机")
    assert calls[0]["kwargs"].get("directory") == "产品文档/交换机"
    assert out["uploads"][0]["directory"] == "产品文档/交换机"


def test_manage_files_respects_forwarded_proto(monkeypatch) -> None:
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
    out = server.manage_files(action="upload", kb_name="网络手册库",
                              filenames=["a.md"])
    assert out["uploads"][0]["upload_url"].startswith("https://kb.example.com/upload/")


def test_manage_files_rejects_path_like_filename(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    with pytest.raises(ToolError, match="filename 非法"):
        server.manage_files(action="upload", kb_name="网络手册库",
                            filenames=["../evil.md"])
    with pytest.raises(ToolError, match="filename 非法"):
        server.manage_files(action="upload", kb_name="网络手册库",
                            filenames=["a/b.md"])
    with pytest.raises(ToolError, match="不能为空"):
        server.manage_files(action="upload", kb_name="网络手册库", filenames=[])


def test_manage_files_rejects_unknown_action(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    with pytest.raises(ToolError, match="action"):
        server.manage_files(action="delete", kb_name="网络手册库",
                            filenames=["a.md"])


def test_manage_files_upload_forbids_replace_params(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    with pytest.raises(ToolError, match="只用于 replace"):
        server.manage_files(action="upload", kb_name="网络手册库",
                            filenames=["a.md"], document_id="abc123")
    with pytest.raises(ToolError, match="只用于 replace"):
        server.manage_files(action="upload", kb_name="网络手册库",
                            filenames=["a.md"], expected_revision=4)


def test_manage_files_upload_rejects_bad_directory_shape(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    for bad in ("a\\b", "/绝对", "a//b", "a/./b", "a/../b"):
        with pytest.raises(ToolError, match="directory"):
            server.manage_files(action="upload", kb_name="网络手册库",
                                filenames=["a.md"], directory_path=bad)


# ── 58号D2：replace 模式（document_id 直连——不经 serving 反查 doc_） ───────


def _patch_for_replace(monkeypatch, *, calls=None):
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    monkeypatch.setattr(
        server.backend, "get_document",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("replace 不得经 serving get_document 反查 doc_")))
    monkeypatch.setattr(
        server.backend, "begin_upload",
        lambda *a, **k: (calls.append({"args": a, "kwargs": k}) if calls is not None
                         else None,
                         {"ticket": "up_r", "max_bytes": 1, "expires_in": 600})[1])
    monkeypatch.setattr(
        server, "get_http_headers", lambda include=None: {"host": "kb.example.com"})
    return calls


def test_manage_files_replace_passes_document_id_directly(monkeypatch) -> None:
    calls: list[dict] = []
    _patch_for_replace(monkeypatch, calls=calls)
    out = server.manage_files(
        action="replace", kb_name="网络手册库", filenames=["手册.pdf"],
        document_id="doc-internal-1", expected_revision=4)
    assert out["uploads"][0]["mode"] == "replace"
    assert out["uploads"][0]["document_id"] == "doc-internal-1"
    begin = calls[0]
    assert begin["kwargs"].get("document_id") == "doc-internal-1"
    assert begin["kwargs"].get("expected_revision") == 4
    assert "directory" not in begin["kwargs"]


def test_manage_files_replace_requires_revision_and_single_file(monkeypatch) -> None:
    _patch_for_replace(monkeypatch)
    with pytest.raises(ToolError, match="必须传 expected_revision"):
        server.manage_files(action="replace", kb_name="网络手册库",
                            filenames=["a.pdf"], document_id="abc123")
    with pytest.raises(ToolError, match="必须传 document_id"):
        server.manage_files(action="replace", kb_name="网络手册库",
                            filenames=["a.pdf"], expected_revision=1)
    with pytest.raises(ToolError, match="恰好一个"):
        server.manage_files(action="replace", kb_name="网络手册库",
                            filenames=["a.pdf", "b.pdf"],
                            document_id="abc123", expected_revision=1)
    with pytest.raises(ToolError, match="非负整数"):
        server.manage_files(action="replace", kb_name="网络手册库",
                            filenames=["a.pdf"], document_id="abc123",
                            expected_revision=-1)
    with pytest.raises(ToolError, match="非负整数"):
        server.manage_files(action="replace", kb_name="网络手册库",
                            filenames=["a.pdf"], document_id="abc123",
                            expected_revision="4")


def test_manage_files_replace_forbids_directory(monkeypatch) -> None:
    _patch_for_replace(monkeypatch)
    with pytest.raises(ToolError, match="只用于 upload"):
        server.manage_files(action="replace", kb_name="网络手册库",
                            filenames=["a.pdf"], document_id="abc123",
                            expected_revision=4, directory_path="产品文档")


def test_doc_view_keeps_document_id(monkeypatch) -> None:
    """58号：doc_ 分支的 source 保留 document_id——它是文件管理身份（manage_files
    替换入口），与 doc_（内容引用）并存不互斥；revision 保留=替换暗号。
    （57号"剥内部 id"决定已被 58号推翻。）"""
    _patch_backend(monkeypatch)
    monkeypatch.setattr(
        server.backend, "get_document",
        lambda *a, **k: {"source": {"document_id": "d-9", "content_revision": 2,
                                    "file_name": "x.pdf"},
                         "segments": []})
    out = server.get_knowledge(ref="doc_X")
    assert out["view"] == "document_content"
    assert out["source"]["document_id"] == "d-9"
    assert out["source"]["content_revision"] == 2


def test_manage_files_limits_filename_count_and_length(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    with pytest.raises(ToolError, match="最多"):
        server.manage_files(
            action="upload", kb_name="网络手册库",
            filenames=[f"{index}.md" for index in range(101)],
        )
    with pytest.raises(ToolError, match="过长"):
        server.manage_files(
            action="upload", kb_name="网络手册库",
            filenames=["x" * 256],
        )


def test_manage_files_rejects_kb_not_open(monkeypatch) -> None:
    monkeypatch.setattr(server, "_identity", lambda: SINGLE)
    with pytest.raises(ToolError, match="未开放或不存在"):
        server.manage_files(action="upload", kb_name="别的库", filenames=["a.md"])


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
