"""FastMCP 3.x server —— 用户级 MCP（批次8 R8 终态：九件套工具族）。

一个服务进程，按密钥"变脸"：每个用户看到自己的工具开关、自己的开放库、
自己改过的提示词与工具描述。鉴权/个性化统一在 middleware 层：
- 无钥/错钥：tools/list 返回空清单、一切调用拒绝（不再匿名可见——批次5 遗留收口）
- on_initialize：把用户的自定义 instructions 注入握手响应
- on_list_tools：按开关过滤 + 描述文案替换
- on_call_tool：开关检查；identity 注入 ContextVar 供工具函数取用

工具族（25 号 §8）：
- 模糊发现：search_knowledge（纯 EvidenceResponse：query/evidence/has_more）
- 渐进闭环：get_evidence → inspect_knowledge → navigate_structure /
  query_structured_asset / get_document（"先 search 拿 ref → inspect 看能力 → 下钻"）
- 管理浏览：list_knowledge_bases / list_documents / upload_document
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

使用 search_knowledge 检索指定知识域中的证据。每次调用都必须显式指定 domain，
不得使用隐式默认领域，也不得根据问题内容擅自猜测领域。如果无法确定 domain，
先要求调用者明确选择知识域。

知识库范围由密钥主人配置（开放哪些库）：只开放一个库时不必传 kb_names，直接检索
即可；开放多个库时可传 kb_names 缩小范围。检索范式跟随知识库绑定自动选择。

检索返回的是证据列表（evidence），每条带 ref / type / content / source。工作流是
渐进式的：先 search_knowledge 模糊找 → 对结果里的 structure_ref / document_ref 用
inspect_knowledge 看真实能力（可导航关系、表格 schema、可过滤/聚合字段）→ 需要
精确结构就用 navigate_structure（章节/父子/前后导航）或 query_structured_asset
（对表格过滤/排序/聚合）→ 证据内容被截断（truncated=true）时用 get_evidence 取
完整原文；需要整个文件时用 get_document（按 document_ref 分页读取）。工具返回的
错误带稳定 code（如 unknown_field / out_of_scope / expired_ref），按提示修正参数
重试即可。

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
        call_next: CallNext[ListToolsRequest, object],
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
            hint = "可退回 search_knowledge / get_evidence。"
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
    domain: str,
    kb_names: list[str] | None = None,
    within: dict | None = None,
    filters: dict | None = None,
    expansion: dict | None = None,
    top_k: int | None = None,
    paradigm: str | None = None,
    debug: bool = False,
) -> dict:
    """按指定知识域检索知识证据，返回证据列表（evidence：ref/type/content/source）。

    检索范围与管线都是自动的：范围 = 密钥主人开放的库；管线 = 目标库绑定的检索范式
    （未绑定时官方默认兜底）。返回只有 query / evidence / has_more——不要期望 score
    或内部 id。

    Args:
        query: 用户原问题。
        domain: 必填知识域标识，例如 civil_engineering 或 odn。
        kb_names: 可选，在开放的多个库中缩小范围。不传 = 检索全部开放库。
        within: 可选范围约束（hard filter）：{"document_refs": ["doc_…"],
            "section_refs": ["st_…"]}。doc_/st_ 可直接传 search/inspect 返回的
            opaque ref（服务端解码为内部范围）。只支持这两个键——其他键
            （如 structure_ref/include_descendants）会返回 400。
        filters: 可选过滤（hard filter）：{"asset_types": ["table"],
            "evidence_types": ["table_row"]}。当前只支持这两个键；路径/日期
            过滤尚未提供，传入会返回 400（不支持显式报错，不静默忽略）。
        expansion: 可选展开模式 {"mode": "auto|exact|window|parent|whole_document"}，
            控制 evidence 内容的粒度（默认 auto）。
        top_k: 可选结果面上限（1-200，服务端按各阶段上限收敛）。
        paradigm: 一般不需要传——范式跟随知识库绑定自动选择。
        debug: 是否返回检索过程诊断信息（true 时响应附 diagnostics 字段）。
    """
    ident = _identity()
    kb_ids = _scope_kbs(ident, kb_names)
    inp = SearchInput(
        query=query, domain=domain, paradigm=paradigm,
        within=within, filters=filters, expansion=expansion, top_k=top_k, debug=debug,
    )
    return _search_knowledge(inp, ident, kb_ids)


@mcp.tool()
def get_evidence(ref: str, domain: str, kb_names: list[str] | None = None,
                 mode: str | None = None) -> dict:
    """取回某条证据的**完整原文**（search 结果里 truncated=true 或需要更大粒度时用）。

    只需把 search_knowledge 返回的 ev_ ref 原样传回，不需要 type/id 等内部标识。

    Args:
        ref: search 结果 evidence[].ref（ev_ 开头）。
        domain: 与产生该 ref 的那次检索相同的知识域。
        kb_names: 可选，ref 所在库的范围（默认全部开放库）。
        mode: 可选展开粒度 auto|exact|window|parent|whole_document（默认 auto=
            预算内就大：父章节/整文优先）。
    """
    ident = _identity()
    kb_ids = _scope_kbs(ident, kb_names)
    return _serving_call(backend.get_evidence, ident.username, kb_ids, domain, ref, mode)


@mcp.tool()
def get_document(document_ref: str, domain: str, kb_names: list[str] | None = None,
                 limit: int | None = None, cursor: str | None = None) -> dict:
    """按 document_ref 读取整个文件的结构化章节（章节 outline + 切片分页）。

    document_ref 来自 search 结果的 source.document_ref（doc_ 开头）或 inspect_knowledge
    的返回。分页：把响应里的 cursor 原样传回即可取下一页。

    Args:
        document_ref: doc_ 开头的文档引用（不需要知识库名或内部文档 id）。
        domain: 该文档所属知识域。
        kb_names: 可选库范围。
        limit: 每页切片数（≤200，默认 100）。
        cursor: 上一页返回的 cursor；不传从第一页开始。
    """
    ident = _identity()
    kb_ids = _scope_kbs(ident, kb_names)
    return _serving_call(backend.get_document, ident.username, kb_ids, domain,
                         document_ref, limit, cursor)


@mcp.tool()
def inspect_knowledge(ref: str, domain: str, kb_names: list[str] | None = None) -> dict:
    """查看一个 ref 背后的真实能力：可导航关系、表格 schema、可过滤/聚合字段。

    输入 search 结果里的任意 ref（evidence[].ref / structure_ref / source.document_ref）
    都可以。返回 capabilities（can_navigate / can_query_structured / can_aggregate）、
    允许的 relations、表格资产清单（带可直接用于 query_structured_asset 的 asset ref）
    与字段 display name/type/operations。**下钻前先 inspect**——比盲试省得多。

    Args:
        ref: ev_/st_/doc_ 任意一种 ref。
        domain: 该 ref 所属知识域。
        kb_names: 可选库范围。
    """
    ident = _identity()
    kb_ids = _scope_kbs(ident, kb_names)
    return _serving_call(backend.inspect_knowledge, ident.username, kb_ids, domain, ref)


@mcp.tool()
def navigate_structure(ref: str, relation: str, domain: str,
                       kb_names: list[str] | None = None, depth: int | None = None,
                       limit: int | None = None, cursor: str | None = None) -> dict:
    """沿白名单结构关系导航：st_ ref → 相邻/父子/祖先/后代节点摘要列表。

    允许的 relation（先 inspect_knowledge 确认目标支持哪些）：
    parent / children / previous / next / ancestors / descendants / container /
    caption / footnotes / references。返回节点的公开 st_ ref，可继续导航。

    Args:
        ref: st_ 开头的结构引用（search 结果的 structure_ref 或上次导航结果）。
        relation: 白名单关系之一。
        domain: 该 ref 所属知识域。
        kb_names: 可选库范围。
        depth: ancestors/descendants 的层数（默认 1，上限 3）。
        limit: 返回条数（默认 50，上限 200）。
        cursor: 分页 cursor（原样传回上一页返回值）。
    """
    ident = _identity()
    kb_ids = _scope_kbs(ident, kb_names)
    return _serving_call(backend.navigate_structure, ident.username, kb_ids, domain,
                         ref, relation, depth, limit, cursor)


@mcp.tool()
def query_structured_asset(asset_ref: str, query: dict, domain: str,
                           kb_names: list[str] | None = None) -> dict:
    """对表格类资产做 schema-bound 精确查询：过滤/投影/排序/聚合（不是模糊搜索）。

    字段名、类型、可用操作来自 inspect_knowledge（asset 的 columns[].name 与
    operations），不是内部列名。未知字段/类型不符会返回带 code 的错误与可用字段清单，
    按提示修正即可。

    Args:
        asset_ref: st_ 开头的表格资产引用（search 结果 structure_ref 或 inspect 返回
            的 assets[].ref）。
        query: 查询 DSL，例如 {"select": ["型号", "最大功耗"],
            "where": [{"field": "最大功耗", "op": "lte", "value": 100}],
            "order_by": [{"field": "最大功耗", "direction": "asc"}], "limit": 20}；
            聚合用 {"aggregate": {"op": "avg", "field": "最大功耗"}, "where": [...]}。
        domain: 该资产所属知识域。
        kb_names: 可选库范围。
    """
    ident = _identity()
    kb_ids = _scope_kbs(ident, kb_names)
    return _serving_call(backend.query_structured_asset, ident.username, kb_ids, domain,
                         asset_ref, query)


@mcp.tool()
def list_knowledge_bases() -> dict:
    """列出密钥主人对本 MCP 开放的知识库。

    Returns:
        knowledge_bases: [{id, name, description}]。检索时不传 kb_names 就是搜全部
        这些库；只开放一个库时 search_knowledge 连库名都不用传。
    """
    ident = _identity()
    try:
        return backend.list_knowledge_bases(ident.username)
    except backend.ToolBackendError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool()
def list_documents(kb_name: str, limit: int = 50, offset: int = 0) -> dict:
    """列出某个开放知识库里的文件清单（名称/状态/大小，分页）。

    Args:
        kb_name: 知识库名称（list_knowledge_bases 里的 name）。
        limit: 每页条数（≤200）。
        offset: 分页偏移。
    """
    ident = _identity()
    kb_id = _resolve_open_kb(ident, kb_name)
    try:
        return backend.list_documents(ident.username, kb_id, limit, offset)
    except backend.ToolBackendError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool()
def upload_document(kb_name: str, filename: str, content_b64: str) -> dict:
    """上传一个文件到开放的知识库（base64 编码内容，≤50MB）。

    **上传不会自动挖掘**：文件入库后需密钥主人在平台界面发起挖掘，内容才可被
    检索到。上传需要对该库有编辑权限。

    Args:
        kb_name: 目标知识库名称。
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
