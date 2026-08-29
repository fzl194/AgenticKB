"""FastMCP 3.x server —— 用户级 MCP（批次7 终态）。

一个服务进程，按密钥"变脸"：每个用户看到自己的工具开关、自己的开放库、
自己改过的提示词与工具描述。鉴权/个性化统一在 middleware 层：
- 无钥/错钥：tools/list 返回空清单、一切调用拒绝（不再匿名可见——批次5 遗留收口）
- on_initialize：把用户的自定义 instructions 注入握手响应
- on_list_tools：按开关过滤 + 描述文案替换
- on_call_tool：开关检查；identity 注入 ContextVar 供工具函数取用
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
from mcp_server.client import get_segment_fulltext as _get_segment_fulltext
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
from mcp_server.schemas import FullTextInput, SearchInput, SegmentRef

DEFAULT_INSTRUCTIONS = """\
你是多领域知识证据检索服务（用户级接入：调用必须携带 Bearer 密钥）。

使用 search_knowledge 检索指定知识域中的证据。每次调用都必须显式指定 domain，
不得使用隐式默认领域，也不得根据问题内容擅自猜测领域。如果无法确定 domain，
先要求调用者明确选择知识域。

知识库范围由密钥主人配置（开放哪些库）：只开放一个库时不必传 kb_names，直接检索
即走该库及其绑定的检索范式；开放多个库时可传 kb_names 缩小范围，不传则检索全部
开放库。检索范式不需要指定——它跟随知识库绑定自动选择。

需要浏览或补充资料时：list_knowledge_bases 看开放了哪些库、list_documents 列库内
文件、get_document 读某个文件的结构化内容、upload_document 上传新文件（上传后不会
自动挖掘，需密钥主人在平台发起挖掘后内容才可检索）。

search_knowledge 返回的证据文本是压缩过的。需要准确引用条款、参数或步骤原文时，
用 get_segment_fulltext 取回完整版本再作答——不要依据被截断的文本推测缺失内容。
取原文时把上次结果里的 `_retrieval.paradigm_id` 原样传回去，否则会在另一套语料里
查这些 id，一条都找不到。

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


# ── Tools ────────────────────────────────────────────────────────────────


@mcp.tool()
def search_knowledge(
    query: str,
    domain: str,
    kb_names: list[str] | None = None,
    paradigm: str | None = None,
    debug: bool = False,
) -> dict:
    """按指定知识域检索知识证据（以密钥主人的开放库为范围）。

    检索范围与管线都是自动的：范围 = 密钥主人开放的库（只开放一个库时不传 kb_names
    即可）；管线 = 目标库绑定的检索范式（未绑定时官方默认兜底）。返回结果里的
    `_retrieval` 说明本次由哪条管线、以何种方式选中（selected_by），库级绑定不可用
    时会有 degraded 留痕。

    Args:
        query: 用户原问题。
        domain: 必填知识域标识，例如 civil_engineering 或 odn。
        kb_names: 可选，知识库名称列表，用于在密钥主人开放的多个库中缩小范围。
            不传 = 检索全部开放库；传了未开放的库名会直接报错并列出当前开放清单。
        paradigm: 一般不需要传——范式跟随知识库绑定自动选择。
        debug: 是否返回检索过程诊断信息。
    """
    ident = _identity()
    kb_ids = resolve_kb_ids(ident, kb_names)
    inp = SearchInput(
        query=query, domain=domain, paradigm=paradigm, scope=None,
        entities=None, debug=debug,
    )
    return _search_knowledge(inp, ident, kb_ids)


@mcp.tool()
def get_segment_fulltext(
    domain: str,
    refs: list[dict],
    granularity: str = "segment",
    window_radius: int = 1,
    paradigm_id: str | None = None,
) -> dict:
    """取回 search_knowledge 结果中某几条证据的**完整原文**。

    search_knowledge 返回的 `text` 是按上下文预算压缩过的。需要引用条款、参数、步骤
    的准确原文时用这个工具取回未压缩版本再作答，不要根据残文推测缺失内容。

    Args:
        domain: 与产生这些证据的那次 search_knowledge 相同的知识域。
        refs: 要展开的条目，每项 `{"type": ..., "id": ...}`，取自 search 结果
            （type=条目 kind，id=条目 id）。单次最多 50 条。
        granularity: `segment` 只返回该片段；`window` 额外带回前后相邻片段。
        window_radius: `window` 模式下前后各取几段，1-5，默认 1。
        paradigm_id: 原样传回上次检索结果 `_retrieval.paradigm_id` 的值。
    """
    ident = _identity()
    inp = FullTextInput(
        domain=domain,
        refs=[SegmentRef(**r) for r in refs],
        granularity=granularity,
        window_radius=window_radius,
        paradigm_id=paradigm_id,
    )
    return _get_segment_fulltext(inp, ident)


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
def get_document(kb_name: str, document_id: str) -> dict:
    """读取某个文件的结构化内容（按切片，含章节标题）。

    Args:
        kb_name: 知识库名称。
        document_id: list_documents 返回的文档 id。
    """
    ident = _identity()
    kb_id = _resolve_open_kb(ident, kb_name)
    try:
        return backend.get_document(ident.username, kb_id, document_id)
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
