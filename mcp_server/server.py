"""FastMCP 3.x server —— 用户级 MCP（2026-08-31 工具族收敛：三件套）。

一个服务进程，按密钥"变脸"：每个用户看到自己的工具开关、自己的开放库、
自己改过的提示词与工具描述。鉴权/个性化统一在 middleware 层：
- 无钥/错钥：tools/list 返回空清单、一切调用拒绝（不再匿名可见——批次5 遗留收口）
- on_initialize：把用户的自定义 instructions 注入握手响应
- on_list_tools：按开关过滤 + 描述文案替换
- on_call_tool：开关检查；identity 注入 ContextVar 供工具函数取用

工具族（用户拍板"功能类似只是维度/层级不同必须合并"——第二轮收敛到三件套）：
- search_knowledge：唯一检索入口（domain 可免传——单域自动默认，多域报错带清单）
- get_knowledge：一切读取行为（ref 分流 ev_/doc_/st_ + 层级浏览 + 能力报告默认），
  合并了 get_content / browse_knowledge / inspect_knowledge / navigate_structure /
  query_structured_asset 五件——Agent 只需知道"有了 ref 或库名就调它"
- upload_document：上传（不变）
"""
from __future__ import annotations

import base64

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.types import CallToolRequestParams, InitializeRequest, ListToolsRequest, Tool

from mcp_server import __version__
from mcp_server import tools as backend
from mcp_server.client import search_knowledge as _search_knowledge
from mcp_server.identity import (
    Identity,
    IdentityError,
    TOOL_NAMES,
    current_identity,
    require_current_identity,
    require_identity,
    resolve_kb_ids,
)
from mcp_server.schemas import SearchInput

DEFAULT_INSTRUCTIONS = """\
你是多领域知识证据检索服务（用户级接入：调用必须携带 Bearer 密钥）。

只有三个工具：search_knowledge 模糊找、get_knowledge 深入读、upload_document 上传。

知识按三层组织：知识域（domain）→ 知识库（knowledge base）→ 文档（document）。
密钥主人决定开放哪些知识库；一台部署通常只有一个 domain——domain 参数可不传，
开放库只覆盖一个域时自动使用；跨多域时返回错误并列出可用域，选一个重试。
不要根据问题内容猜测领域。

工作流：先用 get_knowledge 不带参数看自己有什么（返回域→库树），或直接
search_knowledge 模糊检索（返回证据列表 evidence，每条带 ref/type/content/source）。
之后一切深入都走 get_knowledge——它按你给的入口自动分流：
- ref 是 ev_（search 结果 evidence[].ref）：给内容原文，truncated=true 时加 mode
  选更大粒度 auto/exact/window/parent/whole_document；
- ref 是 doc_（source.document_ref）：limit/cursor 分页读整篇文档；
- ref 是 st_（structure_ref）：只传 ref 给能力报告（可导航关系、表格 schema、
  可过滤聚合字段）；要查表格传 query（DSL：select/where/order_by/limit/
  aggregate，字段名以能力报告的 columns 为准）；要沿结构走传 relation
  （parent/children/previous/next/ancestors/descendants/container/caption/
  footnotes/references）。
工具返回的错误带稳定 code（如 unknown_field / out_of_scope / expired_ref），
按提示修正参数重试即可。

回答时应区分证据直接支持的内容、基于证据的推断，以及当前缺失或不确定的信息；
不得编造命令、参数、约束、依赖或步骤。
"""


def _identity_or_none() -> Identity | None:
    try:
        return require_identity(get_http_headers(include={"authorization"}))
    except IdentityError:
        return None


class PersonalizationMiddleware(Middleware):
    """用户级鉴权与个性化：清单过滤、描述替换、开关拦截、提示词注入。"""

    async def on_initialize(
        self,
        context: MiddlewareContext[InitializeRequest],
        call_next: CallNext[InitializeRequest, object],
    ):
        ident = _identity_or_none()
        # 已知限制（fastmcp 3.4.7）：initialize 响应在 middleware 返回路径之外组装
        # （见 fastmcp/server/low_level.py 的 capture 注释），pre-set 实例属性不反映。
        # 自定义 instructions 暂存不注入——工具描述动态化（on_list_tools）已生效，
        # 那才是 Agent 选工具的主要依据；升级 fastmcp 后收口本项。
        _ = ident
        return await call_next(context)

    async def on_list_tools(
        self,
        context: MiddlewareContext[ListToolsRequest],
        call_next: CallNext[ListToolsRequest, Tool],
    ):
        tools = await call_next(context)
        ident = _identity_or_none()
        if ident is None:
            # 无有效身份：连清单都拿不到（强制密钥的一部分）
            return []
        enabled = ident.enabled_tools()
        out: list[Tool] = []
        for t in tools:
            if t.name not in TOOL_NAMES:
                out.append(t)  # 非工具族项（如未来内置诊断工具）不受开关管理
                continue
            if t.name not in enabled:
                continue
            replaced = ident.tool_description(t.name, t.description)
            if replaced and replaced != t.description:
                t = t.model_copy(update={"description": replaced})
            out.append(t)
        return out

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, object],
    ):
        try:
            ident = require_identity(get_http_headers(include={"authorization"}))
        except IdentityError as exc:
            raise ToolError(str(exc)) from None
        name = context.message.name
        if name in TOOL_NAMES and not ident.tool_enabled(name):
            raise ToolError(f"工具 {name} 未开放：密钥主人已在「MCP 接入」页关闭它。")
        current_identity.set(ident)
        return await call_next(context)


mcp = FastMCP(
    "multi-domain-knowledge",
    instructions=DEFAULT_INSTRUCTIONS,
    middleware=[PersonalizationMiddleware()],
)


def _identity() -> Identity:
    return require_current_identity()


def _domain(ident: Identity, explicit: str | None) -> str:
    """domain 参数缺省解析：单域自动 / 多域带清单报错 / 显式优先且校验。"""
    try:
        return ident.resolve_domain(explicit)
    except IdentityError as exc:
        raise ToolError(str(exc)) from None


def _resolve_open_kb(ident: Identity, kb_name: str) -> str:
    """按名称在开放库中解析 id（大小写不敏感；报错带开放清单）。"""
    if not ident.open_kbs:
        raise ToolError("当前 MCP 未开放任何知识库：请密钥主人在「MCP 接入」页勾选。")
    key = str(kb_name).strip().casefold()
    for k in ident.open_kbs:
        if str(k["name"]).strip().casefold() == key:
            return str(k["id"])
    names = "、".join(k["name"] for k in ident.open_kbs)
    raise ToolError(f"知识库 {kb_name!r} 未开放或不存在。当前开放：{names}。")


def _scope_kbs(ident: Identity, kb_names: list[str] | None) -> list[str]:
    """结构工具的库范围：未传 kb_names = 全部开放库（ref 授权按此求交）。"""
    try:
        return resolve_kb_ids(ident, kb_names)
    except IdentityError as exc:
        raise ToolError(str(exc)) from None


def _serving_call(call, *args, **kwargs):
    """统一把 serving 结构工具的 typed error 转成 Agent 可修正的 ToolError。"""
    try:
        return call(*args, **kwargs)
    except backend.ServingToolError as exc:
        hint = ""
        if exc.code == "unknown_field":
            allowed = exc.details.get("allowed_fields") or []
            if allowed:
                hint = f"可用字段：{'、'.join(str(a) for a in allowed[:20])}。"
        elif exc.code == "structured_query_unavailable":
            hint = "可退回 search_knowledge，或只传 ref 给 get_knowledge 看能力报告。"
        elif exc.code in ("expired_ref", "out_of_scope"):
            hint = "请重新 search_knowledge 获取新 ref。"
        elif exc.code == "result_too_large":
            hint = "请缩小范围、增加过滤条件或使用 cursor 分页。"
        raise ToolError(f"[{exc.code}] {exc}。{hint}") from None
    except backend.ToolBackendError as exc:
        raise ToolError(str(exc)) from None


# ── Tools ────────────────────────────────────────────────────────────────


@mcp.tool()
def search_knowledge(
    query: str,
    domain: str | None = None,
    kb_names: list[str] | None = None,
    within: dict | None = None,
    filters: dict | None = None,
    expansion: dict | None = None,
    top_k: int | None = None,
    paradigm: str | None = None,
    debug: bool = False,
) -> dict:
    """检索知识证据，返回证据列表（evidence：ref/type/content/source）——一切检索的起点。

    检索范围与管线都是自动的：范围 = 密钥主人开放的库；管线 = 目标库绑定的检索范式
    （未绑定时官方默认兜底）。返回只有 query / evidence / has_more——不要期望 score
    或内部 id。

    Args:
        query: 用户原问题。
        domain: 可选知识域。不传时若开放库只覆盖一个域则自动使用该域（最常见）；
            跨多域时报错并列出可用域，选一个重试。不要按问题内容猜领域。
        kb_names: 可选，在开放的多个库中缩小范围。不传 = 检索全部开放库。
        within: 可选范围约束（hard filter）：{"document_refs": ["doc_…"],
            "section_refs": ["st_…"]}。doc_/st_ 可直接传 search/inspect 返回的
            opaque ref（服务端解码为内部范围）。只支持这两个键——其他键
            （如 structure_ref/include_descendants）会返回 400。
        filters: 可选过滤（hard filter）：{"asset_types": ["table"],
            "evidence_types": ["table_row"]}。evidence_types 用公开类型词
            （prose/section/document/table/table_row/list/code/formula/
            figure_caption——即 search 返回 evidence[].type 的取值，可原样
            回传筛选）。当前只支持这两个键；路径/日期过滤尚未提供，传入会
            返回 400（不支持显式报错，不静默忽略）。
        expansion: 可选展开模式 {"mode": "auto|exact|window|parent|whole_document"}，
            控制 evidence 内容的粒度（默认 auto）。
        top_k: 可选结果面上限（1-200，服务端按各阶段上限收敛）。
        paradigm: 一般不需要传——范式跟随知识库绑定自动选择。
        debug: 是否返回检索过程诊断信息（true 时响应附 diagnostics 字段）。
    """
    ident = _identity()
    kb_ids = _scope_kbs(ident, kb_names)
    resolved = _domain(ident, domain)
    inp = SearchInput(
        query=query, domain=resolved, paradigm=paradigm,
        within=within, filters=filters, expansion=expansion, top_k=top_k, debug=debug,
    )
    return _search_knowledge(inp, ident, kb_ids)


@mcp.tool()
def get_knowledge(
    ref: str | None = None,
    kb_name: str | None = None,
    domain: str | None = None,
    mode: str | None = None,
    relation: str | None = None,
    query: dict | None = None,
    depth: int | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    offset: int | None = None,
    kb_names: list[str] | None = None,
) -> dict:
    """深入读取知识——一切读取行为都在这一个工具里，按你给的入口自动分流。

    知识层级：知识域（domain）→ 知识库（knowledge base）→ 文档（document）→
    证据（evidence）→ 结构（structure）。入口优先级：ref > kb_name > 空。

    分流矩阵（返回都带 "view" 字段自标识）：
    | 你给的入口                        | 行为                     | view            |
    |-----------------------------------|--------------------------|-----------------|
    | 什么都不传                        | 域→库 顶层浏览           | kb_tree         |
    | 只传 kb_name                      | 该库文件清单（分页）     | documents       |
    | ref=ev_（mode 可选）              | 证据原文展开             | evidence_content|
    | ref=doc_（limit/cursor 可选）     | 整篇文档分页             | document_content|
    | 只传 ref=st_                      | **能力报告**（默认）：   | capabilities    |
    |                                   | 可导航关系/表格 schema/  |                 |
    |                                   | 可过滤聚合字段+下一步提示|                 |
    | ref=st_ + query                   | 表格精确查询（DSL）      | table_rows/     |
    |                                   |                          | aggregate       |
    | ref=st_ + relation                | 结构关系导航             | navigation      |

    ev_/doc_ 是内容引用——只传 ref 就直接给内容（默认粒度/首页）；st_ 是结构
    引用——只传 ref 给能力报告，告诉你 relation/query 能传什么。ev_ 来自 search
    结果 evidence[].ref（truncated=true 时加 mode 取全）；doc_ 来自
    source.document_ref；st_ 来自 structure_ref 或导航结果。

    Args:
        ref: 上游返回的引用。ev_ → 可用 mode；doc_ → 可用 limit/cursor；
            st_ → 可用 query 或 relation；只传它 = 能力报告。
        kb_name: 要看的目标知识库名（顶层浏览返回的 name）——传了列该库文件清单，
            不能与 ref 同时传。
        domain: 知识域。ref 分支 = 路由域（不传自动：单域默认，跨多域报错带清单）；
            顶层分支 = 只看该域。
        mode: 仅 ref=ev_ 有效：展开粒度 auto|exact|window|parent|whole_document
            （默认 auto=预算内就大：父章节/整文优先）。truncated=true 的证据取全用。
        relation: 仅 ref=st_ 有效：parent/children/previous/next/ancestors/
            descendants/container/caption/footnotes/references 之一（能力报告的
            relations 列出目标支持哪些）。
        query: 仅 ref=st_（表格资产）有效：DSL {"select": ["列名"],
            "where": [{"field":"列名","op":"lte","value":100}],
            "order_by": [{"field":"列名","direction":"asc"}], "limit": 20}；
            聚合 {"aggregate": {"op":"avg","field":"列名"}, "where":[…]}。
            字段名以能力报告 assets[].columns[].name 为准——不是模糊搜索。
        depth: 仅 relation=ancestors/descendants：层数（默认 1，上限 3）。
        limit: 条数上限：doc_ 每页切片（≤200 默认100）/ navigation 条数（≤200
            默认50）/ documents 每页（≤200 默认50）。
        cursor: 分页游标：上一页返回的 cursor 原样传回（doc_ 与 navigation）。
        offset: 仅 documents 视图：分页偏移。
        kb_names: 仅 ref 分支：限定库范围（与 search_knowledge 的 kb_names 同义，
            默认全部开放库）。注意与 kb_name（浏览目标库）是两回事。
    """
    ident = _identity()
    has_ref = bool(ref and str(ref).strip())
    has_kb = bool(kb_name and str(kb_name).strip())
    if has_ref and has_kb:
        raise ToolError("ref 与 kb_name 不能同时传：ref=深入某个引用，kb_name=浏览某个库。")
    if has_ref:
        return _get_by_ref(ident, str(ref), domain, kb_names,
                           mode, relation, query, depth, limit, cursor)
    if has_kb:
        return _list_kb_documents(ident, str(kb_name), limit, offset)
    return _browse_top(ident, domain)


def _get_by_ref(ident: Identity, ref: str, domain: str | None,
                kb_names: list[str] | None, mode: str | None,
                relation: str | None, query: dict | None,
                depth: int | None, limit: int | None, cursor: str | None) -> dict:
    """ref 分流：ev_ 内容 / doc_ 分页 / st_ 按 query|relation|能力报告。"""
    kb_ids = _scope_kbs(ident, kb_names)
    resolved = _domain(ident, domain)
    username = ident.username

    if ref.startswith("ev_"):
        if relation or query:
            raise ToolError(
                "ev_ 是证据引用，只支持原文展开（mode 参数）。要导航结构或查表格，"
                "请改传该证据的 structure_ref（st_）。"
            )
        out = _serving_call(backend.get_evidence, username, kb_ids, resolved, ref, mode)
        return {**out, "view": "evidence_content"}

    if ref.startswith("doc_"):
        if relation or query or mode:
            raise ToolError(
                "doc_ 是文档引用，只支持分页读取（limit/cursor）。要导航结构或查表格，"
                "请改传 search 结果里的 structure_ref（st_）。"
            )
        out = _serving_call(
            backend.get_document, username, kb_ids, resolved, ref, limit, cursor)
        return {**out, "view": "document_content"}

    # st_（或其他形状）：query > relation > 能力报告
    if query is not None:
        if relation:
            raise ToolError(
                "query 与 relation 不能同时传：query=查这个表格，relation=沿结构导航。"
            )
        out = _serving_call(
            backend.query_structured_asset, username, kb_ids, resolved, ref, query)
        return {**out, "view": (
            "aggregate" if isinstance(query, dict) and query.get("aggregate")
            else "table_rows")}
    if relation and str(relation).strip():
        if mode:
            raise ToolError("mode（展开粒度）只用于 ev_ 证据引用，与 relation 互斥。")
        out = _serving_call(
            backend.navigate_structure, username, kb_ids, resolved,
            ref, str(relation), depth, limit, cursor)
        return {**out, "view": "navigation"}
    if mode:
        raise ToolError(
            "mode（展开粒度）只用于 ev_ 证据引用。st_ 结构引用请用 relation 导航、"
            "query 查表格，或只传 ref 看能力报告。"
        )
    out = _serving_call(backend.inspect_knowledge, username, kb_ids, resolved, ref)
    return {**out, "view": "capabilities"}


def _list_kb_documents(ident: Identity, kb_name: str,
                       limit: int | None, offset: int | None) -> dict:
    kb_id = _resolve_open_kb(ident, kb_name)
    try:
        out = backend.list_documents(ident.username, kb_id,
                                     limit if limit is not None else 50,
                                     offset or 0)
    except backend.ToolBackendError as exc:
        raise ToolError(str(exc)) from None
    return {**out, "view": "documents"}


def _browse_top(ident: Identity, domain: str | None) -> dict:
    """顶层：开放库按域分组（mining 端点 ∩ 实时可见，含描述；不回内部 id——
    Agent 只需要 name（检索/浏览参数按名称）与 domain（检索参数））。"""
    try:
        listing = backend.list_knowledge_bases(ident.username)
    except backend.ToolBackendError as exc:
        raise ToolError(str(exc)) from None
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for k in (listing.get("knowledge_bases") or []):
        dom = str(k.get("domain") or "")
        entry = {"name": str(k.get("name") or "")}
        if k.get("description"):
            entry["description"] = str(k["description"])
        if dom not in groups:
            groups[dom] = []
            order.append(dom)
        groups[dom].append(entry)
    if domain and str(domain).strip():
        wanted = str(domain).strip()
        order = [d for d in order if d == wanted]
        if not order:
            available = "、".join(
                d if d else "（未分组）" for d in groups) or "（无）"
            raise ToolError(
                f"知识域 {wanted!r} 下没有开放的知识库。当前有库的知识域：{available}。"
            )
    return {
        "view": "kb_tree",
        "domains": [
            {"domain": d, "knowledge_bases": groups[d]} for d in order
        ],
        "default_domain": order[0] if len(order) == 1 else None,
        "hint": (
            "检索用 search_knowledge；只覆盖一个域时 domain 可不传。"
            if len(order) == 1
            else "覆盖多个域：检索时从中选一个作为 domain 参数。"
        ),
    }


@mcp.tool()
def upload_document(kb_name: str, filename: str, content_b64: str) -> dict:
    """上传一个文件到开放的知识库（base64 编码内容，≤50MB）。

    **上传不会自动挖掘**：文件入库后需密钥主人在平台界面发起挖掘，内容才可被
    检索到。上传需要对该库有编辑权限。

    Args:
        kb_name: 目标知识库名称（get_knowledge 顶层浏览返回的 name）。
        filename: 文件名（含扩展名，如 "手册.pdf"；不含路径）。
        content_b64: 文件内容的 base64 编码。
    """
    ident = _identity()
    kb_id = _resolve_open_kb(ident, kb_name)
    try:
        base64.b64decode(content_b64, validate=True)
    except Exception:
        raise ToolError("content_b64 不是有效的 base64 编码。") from None
    try:
        return backend.upload_document(ident.username, kb_id, filename, content_b64)
    except backend.ToolBackendError as exc:
        raise ToolError(str(exc)) from None


__all__ = ["mcp", "__version__"]
