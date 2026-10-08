"""FastMCP 3.x server —— 用户级 MCP（2026-08-31 工具族收敛：三件套）。

一个服务进程，按密钥"变脸"：每个用户看到自己的工具开关、自己的开放库、
自己改过的提示词与工具描述。鉴权/个性化统一在 middleware 层：
- 无钥/错钥：tools/list 返回空清单、一切调用拒绝（不再匿名可见——批次5 遗留收口）
- on_initialize：把用户的自定义 instructions 注入握手响应
- on_list_tools：按开关过滤 + 描述文案替换
- on_call_tool：开关检查；identity 注入 ContextVar 供工具函数取用

工具族（用户拍板"功能类似只是维度/层级不同必须合并"——第二轮收敛到三件套）：
- search_knowledge：唯一检索入口（domain 只是校验参数——不传即钥匙绑定域）
- get_knowledge：一切读取行为（ref 分流 ev_/doc_/st_ + 层级浏览 + 能力报告默认），
  合并了 get_content / browse_knowledge / inspect_knowledge / navigate_structure /
  query_structured_asset 五件——Agent 只需知道"有了 ref 或库名就调它"
- manage_files：文件管理（上传到目录/替换任意本地文件；两步直传：工具发
  一次性 URL，Agent PUT 原始字节；不提供删除/移动/重命名——58号改名自 upload_document）
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid

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
    validate_domain,
)
from mcp_server.schemas import SearchInput
from mcp_server.access_records import (
    ACCESS_RECORD_WRITE_TIMEOUT_SECONDS,
    build_access_payload,
    complete_upload_ticket,
    current_access_call,
    expire_pending_uploads as _expire_pending_uploads,
    get_access_record_metrics,
    increment_access_record_write_failures,
    post_access_record,
    register_upload_tickets,
)

logger = logging.getLogger(__name__)
_post_access_record = post_access_record
MAX_UPLOAD_FILENAMES = 100
MAX_UPLOAD_FILENAME_LENGTH = 255
#: 58号：upload 的 directory_path 长度上限（与 mining/serving 侧 512 对齐）
MAX_DIRECTORY_PATH_LENGTH = 512
_pending_upload_recovery_lock = asyncio.Lock()


async def _recover_pending_uploads_once() -> None:
    """Best-effort idempotent recovery on every initialize across all domains."""

    async with _pending_upload_recovery_lock:
        try:
            expired_count = await _expire_pending_uploads()
        except Exception as exc:
            increment_access_record_write_failures()
            logger.warning(
                "access_record_write_failed",
                extra={
                    "record_id": "",
                    "tool": "manage_files",
                    "phase": "startup_expire",
                    "error_class": exc.__class__.__name__,
                },
            )
            return
        if expired_count:
            logger.info(
                "expired_orphan_upload_records",
                extra={"expired_count": expired_count},
            )

class LedgerToolError(ToolError):
    """ToolError carrying stable, non-sensitive ledger classification."""

    def __init__(
        self, message: str, *, ledger_status: str, error_code: str,
    ) -> None:
        super().__init__(message)
        self.ledger_status = ledger_status
        self.error_code = error_code


def _upstream_tool_error(exc: BaseException) -> LedgerToolError:
    return LedgerToolError(
        str(exc), ledger_status="failed", error_code="upstream_failed",
    )


async def _safe_post_access_record(
    payload: dict, *, phase: str,
) -> None:
    """Keep observability failures bounded and outside the tool result path."""
    async def invoke() -> None:
        outcome = _post_access_record(payload)
        if inspect.isawaitable(outcome):
            await outcome

    try:
        await asyncio.wait_for(
            invoke(), timeout=ACCESS_RECORD_WRITE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        increment_access_record_write_failures()
        logger.warning(
            "access_record_write_failed",
            extra={
                "record_id": str(payload.get("id") or ""),
                "tool": str(payload.get("tool_name") or ""),
                "phase": phase,
                "error_class": exc.__class__.__name__,
            },
        )

DEFAULT_INSTRUCTIONS = """\
你是多领域知识证据检索服务（用户级接入：调用必须携带 Bearer 密钥）。

只有三个工具：search_knowledge 搜内容、get_knowledge 浏览与深读、manage_files
管理文件（上传到目录 / 替换任意本地文件；不提供删除、移动、重命名）。

知识按三层组织：知识域（domain）→ 知识库（knowledge base）→ 文档（document）。
密钥主人决定开放哪些知识库；每把钥匙绑定一个知识域——domain 参数可不传
（自动使用钥匙绑定域），传了也必须等于绑定域，跨域访问请换对应域的钥匙。
不要根据问题内容猜测领域。

文件有两个"名字"，用途不同：
- document_id：文件的身份证（上传即有、永不换）——manage_files 替换文件就认它；
- doc_（document_ref）：某次挖掘快照的内容引用（重挖会变）——get_knowledge
  读整篇内容用它。content_revision 是版本暗号，替换时原样回传防互相覆盖。

工作流：先用 get_knowledge 不带参数看自己有什么（返回域→库树），或
browse_directory="" 逐层看目录；要更新文件就 search_knowledge / get_knowledge
拿到 document_id + content_revision 后走 manage_files(action="replace")。
search_knowledge 模糊检索返回证据列表 evidence（每条带 ref/type/content/source），
source 里同时有 document_id 和 document_ref。之后一切深入都走 get_knowledge：
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
        await _recover_pending_uploads_once()
        ident = _identity_or_none()
        # 按会话注入自定义提示词（fastmcp 3.4.7 路径）：
        # InitializeResult 在 call_next 内部由 SDK 组装并直接发往写流——中间件
        # 拿到的返回值是发送后的捕获对象，事后改写无效；改 FastMCP 实例属性又
        # 会串到其他连接。但每条连接的 ServerSession 持有自己的 _init_options，
        # SDK 组装响应时才读取其中的 instructions——在 call_next 之前按会话改写
        # 即生效且互不串扰。fastmcp 的 Context 在 init 阶段特意保留 session 引用
        # （"For state ops during init"），升级 fastmcp/mcp SDK 时此处需回归验证。
        if ident is not None and ident.instructions:
            options = getattr(context.fastmcp_context.session, "_init_options", None)
            if options is not None:
                options.instructions = ident.instructions
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
        name = context.message.name
        raw_arguments = getattr(context.message, "arguments", None)
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        try:
            ident = require_identity(get_http_headers(include={"authorization"}))
        except IdentityError as exc:
            # 鉴权失败不是用户业务活动，不能污染正式检索账本。
            raise ToolError(str(exc)) from None

        identity_token = current_identity.set(ident)
        if name not in TOOL_NAMES:
            try:
                return await call_next(context)
            finally:
                current_identity.reset(identity_token)

        call_id = str(uuid.uuid4())
        started = time.monotonic()
        result = None
        failure: BaseException | None = None
        forced_status: str | None = None
        access_token = current_access_call.set({"id": call_id})
        try:
            if not ident.tool_enabled(name):
                forced_status = "denied"
                failure = ToolError(f"tool {name} is disabled")
                raise failure
            pending = build_access_payload(
                call_id=call_id,
                tool_name=name,
                arguments=arguments,
                identity=ident,
                result=None,
                failure=None,
                duration_ms=0,
                forced_status="pending",
            )
            await _safe_post_access_record(pending, phase="create")
            result = await call_next(context)
            return result
        except BaseException as exc:
            if failure is None:
                failure = exc
            raise
        finally:
            try:
                duration_ms = int(max(0.0, (time.monotonic() - started) * 1000))
                payload = build_access_payload(
                    call_id=call_id,
                    tool_name=name,
                    arguments=arguments,
                    identity=ident,
                    result=result,
                    failure=failure,
                    duration_ms=duration_ms,
                    forced_status=forced_status,
                )
                if name == "manage_files" and payload["status"] == "pending":
                    register_upload_tickets(payload, result)
                await _safe_post_access_record(payload, phase="complete")
            finally:
                current_access_call.reset(access_token)
                current_identity.reset(identity_token)


mcp = FastMCP(
    "multi-domain-knowledge",
    instructions=DEFAULT_INSTRUCTIONS,
    middleware=[PersonalizationMiddleware()],
)


@mcp.custom_route("/health", methods=["GET"])
async def _health(_request):
    """Process health plus best-effort retrieval-ledger write failures."""
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok", **get_access_record_metrics()})

def _identity() -> Identity:
    return require_current_identity()


def _domain(ident: Identity, explicit: str | None) -> str:
    """M3：domain 只是校验参数——不传=钥匙域；传了必须等于钥匙域。"""
    try:
        return validate_domain(ident, explicit)
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
        status = "denied" if exc.code == "out_of_scope" else "invalid"
        raise LedgerToolError(
            f"[{exc.code}] {exc}。{hint}",
            ledger_status=status,
            error_code=exc.code,
        ) from None
    except backend.ToolBackendError as exc:
        raise _upstream_tool_error(exc) from None


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
        domain: 可选，仅校验：必须等于本钥匙绑定的知识域，不传即钥匙域
            （每把 MCP 钥匙绑定一个域——访问其他域需另配对应域的钥匙）。
        kb_names: 可选，在开放的多个库中缩小范围。不传 = 检索全部开放库。
        within: 可选范围约束（hard filter）：{"document_refs": ["doc_…"],
            "section_refs": ["st_…"]}。doc_/st_ 可直接传 search/inspect 返回的
            opaque ref（服务端解码为内部范围）。只支持这两个键——其他键
            （如 structure_ref/include_descendants）会返回 400。
        filters: 可选过滤（hard filter）：{"asset_types": ["table"],
            "evidence_types": ["table_row"], "directory_prefix": "产品文档/手册"}。
            evidence_types 用公开类型词（prose/section/document/table/table_row/
            list/code/formula/figure_caption——即 search 返回 evidence[].type 的
            取值，可原样回传筛选）。directory_prefix（单个字符串）限定目录及
            全部子目录检索——要"整库内这块业务资料"，从证据 source.relative_path
            或 get_knowledge 文件清单的 directory_path 可得目录写法。其余键
            （路径其余形式/日期）尚未提供，传入会返回 400（不支持显式报错，
            不静默忽略）。
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
    browse_directory: str | None = None,
    file_query: str | None = None,
    directory_prefix: str | None = None,
    status: str | None = None,
    depth: int | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    offset: int | None = None,
    kb_names: list[str] | None = None,
) -> dict:
    """深入读取知识——一切读取行为都在这一个工具里，按你给的入口自动分流。

    知识层级：知识域（domain）→ 知识库（knowledge base）→ 文档（document）→
    证据（evidence）→ 结构（structure）。入口优先级：ref > kb_name > file_query > 空。

    分流矩阵（返回都带 "view" 字段自标识）：
    | 你给的入口                        | 行为                     | view            |
    |-----------------------------------|--------------------------|-----------------|
    | 什么都不传                        | 域→库 顶层浏览           | kb_tree         |
    | kb_name + browse_directory        | **目录逐层浏览**：直属   | directory       |
    |                                   | 子目录（空目录可见）+    |                 |
    |                                   | 直属文件（不递归）       |                 |
    | 只传 kb_name                      | 该库文件清单（分页）     | documents       |
    | kb_name + file_query              | 该库内搜文件             | documents       |
    | 只传 file_query                   | **跨全部开放库搜文件**   | file_results    |
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

    browse_directory 与 directory_prefix 的分工（都用 / 分隔的目录路径，如
    "产品文档/手册"）：browse_directory=**打开文件夹看一眼**（只列本层直属
    子目录与直属文件，空目录可见，根目录传 ""）；directory_prefix=**搜索时
    限定"该目录及全部子目录"的递归过滤**（配 file_query 用）。manage_files
    上传的 directory_path 写法从 browse_directory 的 child_directories[].path
    抄——不自动创建目录，拼错会 422。

    file_query 是**找文件**（按文件名，秒回，不依赖挖掘——未挖掘/挖掘失败的
    文件也能搜到，正是它相对 search_knowledge 的独特价值）；search_knowledge 是
    **搜内容**。文件条目带 document_id/status/content_revision/directory_path
    （+跨库时的 kb），其中 content_revision 是 manage_files(action="replace") 要
    回传的版本暗号（expected_revision），document_id 是替换目标。

    Args:
        ref: 上游返回的引用。ev_ → 可用 mode；doc_ → 可用 limit/cursor；
            st_ → 可用 query 或 relation；只传它 = 能力报告。与 kb_name/file_query
            互斥。
        kb_name: 要看的目标知识库名（顶层浏览返回的 name）——传了列该库文件清单
            （可与 file_query 组合为库内搜索，或与 browse_directory 组合逐层看
            目录），不能与 ref 同时传。
        domain: 可选，仅校验：必须等于本钥匙绑定的知识域，不传即钥匙域
            （顶层浏览始终只展示钥匙域下的开放库）。
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
            行数超 limit 时响应带 cursor/has_more——翻页把工具顶层 cursor 参数
            原样传回（不要自己往 query 里塞 cursor，工具会代劳合并）。
        browse_directory: 目录逐层浏览（须配 kb_name）：" "=库根目录，
            "产品文档/手册"=该目录直属内容（child_directories+documents，
            空目录可见，不递归）。与 ref/file_query/directory_prefix/status 互斥。
        file_query: 文件名关键词（子串匹配）。只传它=跨全部开放库搜文件；与
            kb_name 组合=该库内搜。与 ref 互斥。
        directory_prefix: 可选，文件搜索限定目录（含子目录），如 "产品文档/手册"
            （仅在 file_query/kb_name+file_query 场景生效，单独传会显式报错）。
        status: 可选，文件按状态过滤：uploaded 待挖掘 / mining 挖掘中 /
            mined 已入库 / failed 挖掘失败 / update_failed 更新失败。
            例：kb_name+status=failed = 该库挖失败清单（无需 file_query）；
            两者都不传时过滤参数显式报错（不静默忽略）。
        depth: 仅 relation=ancestors/descendants：层数（默认 1，上限 3）。
        limit: 条数上限：doc_ 每页切片（≤200 默认100）/ navigation 条数（≤200
            默认50）/ documents·file_results·directory 的 documents 每页
            （≤200 默认50）。
        cursor: 分页游标：上一页返回的 cursor 原样传回（doc_ 与 navigation 与
            st_+query 表格行分页——三处通用，不要自行改写）。
        offset: 仅 documents/file_results/directory 视图：分页偏移。
        kb_names: 仅 ref 分支：限定库范围（与 search_knowledge 的 kb_names 同义，
            默认全部开放库）。注意与 kb_name（浏览目标库）是两回事。
    """
    ident = _identity()
    has_ref = bool(ref and str(ref).strip())
    has_kb = bool(kb_name and str(kb_name).strip())
    has_file_query = bool(file_query and str(file_query).strip())
    # 58号§3：browse_directory 与 ref/搜索过滤互斥（浏览是浏览，搜索是搜索）
    if browse_directory is not None:
        if has_ref:
            raise ToolError(
                "browse_directory 与 ref 不能同时传：browse_directory=逐层看目录，"
                "ref=深入某个内容引用。"
            )
        if has_file_query or (directory_prefix and str(directory_prefix).strip()) \
                or (status and str(status).strip()):
            raise ToolError(
                "browse_directory 与 file_query/directory_prefix/status 不能同时传："
                "要搜索文件请去掉 browse_directory 用 file_query（递归限目录加"
                " directory_prefix）；要看目录结构只传 browse_directory。"
            )
        if not has_kb:
            raise ToolError(
                "browse_directory 必须与 kb_name 同传：先明确浏览哪个库"
                "（顶层浏览返回的 name）。"
            )
    if has_ref and (has_kb or has_file_query):
        raise ToolError(
            "ref 与 kb_name/file_query 不能同时传：ref=深入某个引用，kb_name=浏览"
            "某个库，file_query=按文件名找文件。"
        )
    # codex 三审：过滤参数对 ref 分支无意义——显式拒绝，不静默忽略
    if has_ref and ((directory_prefix and str(directory_prefix).strip())
                    or (status and str(status).strip())):
        raise ToolError(
            "directory_prefix/status 是文件搜索过滤参数，与 ref 互斥：要限定"
            "检索目录请用 search_knowledge 的 filters.directory_prefix。"
        )
    if has_ref:
        return _get_by_ref(ident, str(ref), domain, kb_names,
                           mode, relation, query, depth, limit, cursor)
    if browse_directory is not None:
        return _browse_directory(ident, str(kb_name), str(browse_directory),
                                 limit, offset)
    if has_kb:
        return _list_kb_documents(
            ident, str(kb_name),
            str(file_query).strip() if has_file_query else None,
            directory_prefix, status, limit, offset)
    if has_file_query:
        return _search_files_across_kbs(
            ident, str(file_query).strip(), directory_prefix, status, limit, offset)
    # codex P3：过滤参数不能脱离搜索上下文被静默忽略（不支持显式报错的工具哲学）
    if (directory_prefix and str(directory_prefix).strip()) or (status and str(status).strip()):
        raise ToolError(
            "directory_prefix/status 只在 file_query（跨库搜文件）或 kb_name+file_query"
            "（库内搜文件）场景下生效：请同时传 file_query，或去掉过滤参数。"
        )
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
        # 58号：source 保留 document_id（文件管理身份，manage_files 替换入口）——
        # 与 doc_（内容引用）并存；content_revision=替换暗号。（57号"剥内部 id"
        # 决定已推翻：id 非秘密，防探测靠权限检查不靠藏 id。）
        return {**out, "view": "document_content"}

    # st_（或其他形状）：query > relation > 能力报告
    if query is not None:
        if relation:
            raise ToolError(
                "query 与 relation 不能同时传：query=查这个表格，relation=沿结构导航。"
            )
        # 内网 1.1.14 实测修复：serving 契约是 cursor 放在 query 字典内
        # （StructureQueryDsl 白名单键），此前顶层 cursor 参数没有合并进转发
        # payload——表格翻页永远停第一页、响应反复发同一个新游标。
        if isinstance(query, dict) and cursor and str(cursor).strip():
            if query.get("cursor"):
                raise ToolError(
                    "cursor 请用工具顶层 cursor 参数传（上一页响应原样回传），"
                    "不要同时在 query 里塞 cursor。"
                )
            if query.get("aggregate"):
                raise ToolError(
                    "cursor 与 aggregate 不能同时传：聚合一次性返回全量结果，"
                    "没有分页；要翻页请用行查询（select/where）。"
                )
            query = {**query, "cursor": str(cursor)}
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


def _browse_directory(
    ident: Identity, kb_name: str, directory: str,
    limit: int | None, offset: int | None,
) -> dict:
    """58号§3：目录逐层浏览（直属子目录+直属文件）——形状翻译转发 mining 端点。"""
    kb_id = _resolve_open_kb(ident, kb_name)
    try:
        out = backend.browse_directory(
            ident.username, ident.key_id, kb_id, directory,
            limit if limit is not None else 50, offset or 0,
        )
    except backend.ToolBackendError as exc:
        raise _upstream_tool_error(exc) from None
    return {
        **out,
        "view": "directory",
        "hint": (
            "child_directories=直属子目录（path 可直接作 browse_directory 与"
            " manage_files 上传 directory_path 的值）；documents=直属文件"
            "（document_id/content_revision 供 manage_files 替换）。"
            "要看该目录及全部子目录的搜索结果请用 file_query+directory_prefix。"
        ),
    }


def _list_kb_documents(
    ident: Identity, kb_name: str, file_query: str | None,
    directory_prefix: str | None, status: str | None,
    limit: int | None, offset: int | None,
) -> dict:
    """该库文件清单/库内搜索（file_query 可选）。"""
    kb_id = _resolve_open_kb(ident, kb_name)
    try:
        out = backend.list_documents(
            ident.username, ident.key_id, kb_id,
            limit if limit is not None else 50,
            offset or 0,
            query=file_query, directory_prefix=directory_prefix, status=status,
        )
    except backend.ToolBackendError as exc:
        raise _upstream_tool_error(exc) from None
    return {**out, "view": "documents"}


def _search_files_across_kbs(
    ident: Identity, file_query: str,
    directory_prefix: str | None, status: str | None,
    limit: int | None, offset: int | None,
) -> dict:
    """跨全部开放库按文件名搜文件（kb_id=None 由 mining 遍历开放集）。"""
    try:
        out = backend.list_documents(
            ident.username, ident.key_id, None,
            limit if limit is not None else 50,
            offset or 0,
            query=file_query, directory_prefix=directory_prefix, status=status,
        )
    except backend.ToolBackendError as exc:
        raise _upstream_tool_error(exc) from None
    return {
        **out,
        "view": "file_results",
        "hint": (
            "条目带 kb（所在库）与 directory_path；要读内容用 search_knowledge，"
            "要替换文件用 manage_files(action=\"replace\")——document_id + content_revision 即参数。"
        ),
    }


def _browse_top(ident: Identity, domain: str | None) -> dict:
    """顶层：开放库按钥匙域分组（批次2 单域钥匙——分组只含 key_domain，
    不再按 listing 的域聚合多组；不回内部 id，Agent 只需要 name）。"""
    resolved = _domain(ident, domain)  # 校验参数：不传=钥匙域；传了必须相等
    try:
        listing = backend.list_knowledge_bases(ident.username, ident.key_id)
    except backend.ToolBackendError as exc:
        raise _upstream_tool_error(exc) from None
    kbs: list[dict] = []
    for k in (listing.get("knowledge_bases") or []):
        if str(k.get("domain") or "") != resolved:
            continue  # 钥匙绑定单域：其他域的库不出现（防御 listing 脏数据）
        entry = {"name": str(k.get("name") or "")}
        if k.get("description"):
            entry["description"] = str(k["description"])
        kbs.append(entry)
    return {
        "view": "kb_tree",
        "domains": [{"domain": resolved, "knowledge_bases": kbs}],
        "default_domain": resolved,
        "hint": (
            "检索内容用 search_knowledge；按文件名找文件用 get_knowledge 传 "
            "file_query（跨库）；domain 可不传（自动使用本钥匙绑定的知识域）。"
        ),
    }


@mcp.tool()
def manage_files(
    action: str,
    kb_name: str,
    filenames: list[str],
    directory_path: str | None = None,
    document_id: str | None = None,
    expected_revision: int | None = None,
) -> dict:
    """管理知识库文件：上传新文件到指定目录，或替换任意已有文件。当前不提供删除、移动或重命名。

    两种模式（两步直传：本工具发一次性 upload_url，Agent PUT 原始字节——不要 base64）：

    action="upload"（上传一个或多个新文件）：
    - filenames 1~100 个纯文件名（不含路径分隔符）；
    - directory_path 可选：不传/空 = 库根目录；传了 = 必须是该库**已存在**的目录
      （不自动创建——先用 get_knowledge(browse_directory=…) 确认目录写法）；
      ZIP 上传到指定目录时解压落位为 {directory_path}/{压缩包名}/…；
    - 返回每个文件的一次性 PUT URL（10 分钟内单次有效，无需任何认证头）；
    - 上传成功自动排队挖掘，挖完才可被 search_knowledge 检索到。

    action="replace"（替换任意已有本地文件——未挖掘/挖掘失败/已入库都可以）：
    - document_id = get_knowledge 文件清单/目录浏览 或 search_knowledge 证据
      source.document_id 返回的同一个值（不要求文件已挖掘，不需要 doc_）；
    - expected_revision = 你看到的 content_revision（必填，版本暗号）；
    - filenames 恰好一个，扩展名必须与当前文件相同（PDF 换 PDF）；
    - 替换保留原文件名、目录与身份；新版本挖完前旧知识继续可检索；
    - 版本对不上返回 409：有人先改了——重新 get_knowledge 取最新
      content_revision 再试。

    明确声明：本工具不删除文件、不移动文件、不重命名文件；directory_path
    只用于 upload；document_id/expected_revision 只用于 replace；referenced=true
    的外部引用文档不可替换；PUT 用原始字节不是 base64。

    示例一（浏览根目录找目标位置）：
        get_knowledge(kb_name="设备库", browse_directory="")
    示例二（上传到指定目录）：
        manage_files(action="upload", kb_name="设备库",
                     filenames=["交换机手册.pdf"],
                     directory_path="产品文档/交换机")
        → 对返回的 upload_url：curl -X PUT --data-binary @交换机手册.pdf "<url>"
    示例三（替换任意文件——document_id/content_revision 来自①或②）：
        ① get_knowledge(kb_name="设备库", browse_directory="产品文档/交换机")
           → documents[].document_id="abc123", content_revision=4
        ② search_knowledge("端口配置") → evidence[].source.document_id/content_revision
        然后：
        manage_files(action="replace", kb_name="设备库",
                     filenames=["交换机手册.pdf"],
                     document_id="abc123", expected_revision=4)

    大小限制与格式：普通文件 50MB；zip/hdx/chm 归档 500MB（自动解压成多个
    文档，与网页端一致）；可挖掘格式 md/txt/html/pdf/doc(x)/xls(x)/ppt(x)/
    json 及归档，其他格式可上传但挖掘会标记不支持（替换模式不支持 zip）。
    票据超时或 PUT 失败：重调本工具取新 URL 重传即可。操作需要对该库有编辑权限。

    Args:
        action: "upload"（新增文件）或 "replace"（替换已有文件）。
        kb_name: 目标知识库名称（get_knowledge 顶层浏览返回的 name）。
        filenames: 文件名列表（含扩展名，如 ["手册.pdf"]）。upload 1~100 个；
            replace 恰好 1 个。每个文件名不含路径分隔符。
        directory_path: 仅 upload：目标目录（如 "产品文档/交换机"）。不传/空 =
            根目录；必须已存在（不自动创建）。
        document_id: 仅 replace：目标文件的 document_id（get_knowledge/
            search_knowledge 返回的同一个值）。
        expected_revision: 仅 replace：你看到的 content_revision（必填版本暗号）。
    """
    ident = _identity()
    kb_id = _resolve_open_kb(ident, kb_name)

    # ── action 分流与参数矩阵（58号D2：两模式参数互斥，显式报错不静默） ──
    action = str(action or "").strip()
    if action not in ("upload", "replace"):
        raise ToolError(
            f"action 只支持 upload（上传新文件）或 replace（替换已有文件），"
            f"收到 {action!r}。本工具不提供删除/移动/重命名。"
        )
    if not filenames:
        raise ToolError("filenames 不能为空：至少给出一个文件名。")
    if len(filenames) > MAX_UPLOAD_FILENAMES:
        raise ToolError(f"filenames 最多允许 {MAX_UPLOAD_FILENAMES} 个文件名。")
    for filename in filenames:
        if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename or ".." in filename:
            raise ToolError(f"filename 非法：{filename!r}（须为不含路径分隔符的纯文件名）。")
        if len(filename) > MAX_UPLOAD_FILENAME_LENGTH:
            raise ToolError(
                f"filename 过长：最多 {MAX_UPLOAD_FILENAME_LENGTH} 个字符。"
            )

    directory = str(directory_path or "").strip()
    doc_id = str(document_id or "").strip()

    if action == "upload":
        if doc_id or expected_revision is not None:
            raise ToolError(
                "document_id/expected_revision 只用于 replace（替换已有文件）；"
                "上传新文件请不要传它们。"
            )
        _check_directory_shape(directory)
    else:  # replace
        if directory:
            raise ToolError(
                "directory_path 只用于 upload：替换保留原文档目录不变，请不要传。"
            )
        if not doc_id:
            raise ToolError(
                "replace 必须传 document_id（get_knowledge 文件清单/目录浏览或 "
                "search_knowledge 证据 source.document_id 返回的值）。"
            )
        if len(filenames) != 1:
            raise ToolError("替换模式一次只能替换一个文件：filenames 恰好一个元素。")
        if expected_revision is None:
            raise ToolError(
                "replace 必须传 expected_revision（你看到的该文档 content_revision，"
                "版本暗号防并发互相覆盖）。"
            )
        if (isinstance(expected_revision, bool)
                or not isinstance(expected_revision, int) or expected_revision < 0):
            raise ToolError(
                "expected_revision 必须是非负整数（该文档的 content_revision）。"
            )

    headers = get_http_headers(include={"host", "x-forwarded-proto"}) or {}

    def _header(name: str) -> str:
        for key, value in headers.items():
            if str(key).lower() == name:
                return str(value)
        return ""

    host = _header("host")
    proto = _header("x-forwarded-proto") or "http"

    # 按模式携带模式专属参数（upload→directory；replace→document_id+暗号），
    # 不传空值——下游与测试都以"键在=语义在"读 kwargs
    mode_kwargs: dict = (
        {"document_id": doc_id, "expected_revision": expected_revision}
        if action == "replace" else ({"directory": directory} if directory else {})
    )
    uploads = []
    for filename in filenames:
        try:
            issued = backend.begin_upload(
                ident.username, ident.key_id, kb_id, str(filename),
                access_record_id=str((current_access_call.get() or {}).get("id") or ""),
                access_record_total=len(filenames),
                **mode_kwargs,
            )
        except backend.ToolBackendError as exc:
            raise _upstream_tool_error(exc) from None
        # 无 Host 上下文（理论不可达）时退化为相对路径，Agent 自行补全
        upload_url = (
            f"{proto}://{host}/upload/{issued['ticket']}" if host
            else f"/upload/{issued['ticket']}"
        )
        uploads.append({
            "filename": filename,
            "upload_url": upload_url,
            "method": "PUT",
            "content_type": "application/octet-stream",
            "max_bytes": issued.get("max_bytes"),
            "expires_in": issued.get("expires_in"),
            **({"mode": "replace", "document_id": doc_id} if action == "replace" else {}),
            **({"directory": directory} if action == "upload" and directory else {}),
        })
    usage = (
        "对每个文件执行：curl -X PUT --data-binary @<本地文件路径> <该文件的 upload_url>"
        "（原始字节，不要 base64；无需任何认证头，URL 即一次性凭证，10 分钟内单次有效）；"
        "每个 PUT 的响应就是该文件的上传与挖掘入队结果。"
        + ("版本对不上返回 409：重新 get_knowledge 取最新 content_revision 再试。"
           if action == "replace" else "")
    )
    return {
        "uploads": uploads,
        "usage": usage,
        **({"batch_tip": (
            "多个小文件也可打包成一个 zip 上传单个 URL，服务端自动解压成多个文档"
            "（与网页端上传 zip 一致）。"
        )} if action == "upload" else {}),
    }


def _check_directory_shape(directory: str) -> None:
    """58号D2：upload 的 directory 形状前置检查（存在性由 begin-upload 查库校验）。"""
    if not directory:
        return
    if "\\" in directory or directory.startswith("/"):
        raise ToolError(
            "directory_path 非法：路径分隔符只认 /，且不能以 / 开头（绝对路径）。"
        )
    if len(directory) > MAX_DIRECTORY_PATH_LENGTH:
        raise ToolError(
            f"directory_path 过长：最多 {MAX_DIRECTORY_PATH_LENGTH} 个字符。"
        )
    for segment in directory.split("/"):
        if segment in ("", ".", ".."):
            raise ToolError(
                "directory_path 非法：含空段或点段（./..）；根目录请不传或传空字符串。"
            )


@mcp.custom_route("/upload/{ticket}", methods=["PUT"])
async def _direct_upload(request):
    """Agent 直传落点：票据即凭证（presigned 模型），流式转发给 mining。

    不验 MCP 密钥：密钥只存在于 MCP 客户端配置（模型不可见），Agent 的
    out-of-band PUT 拿不到它。票据本身 192bit 随机、TTL 10 分钟、单次
    使用、绑定 库/用户/文件名——泄露面收敛为"10 分钟内替某人传一个指定
    名字的文件"，与 S3/MinIO 预签名 URL 同一信任模型。归属用户由票据
    绑定值决定（mining 侧消费），本路由不做二次身份判定。
    """
    from starlette.responses import JSONResponse

    ticket = str(request.path_params.get("ticket") or "")
    # 提前拒绝明显超限的请求体；权威上限在 mining 按票据（普通文件 50MB /
    # 归档 500MB）流式强制。这里用归档上限做粗过滤，避免误拒合法大包。
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > 500 * 1024 * 1024:
        await _finish_upload_access(
            ticket, success=False, error_code="upload_too_large")
        return JSONResponse({"detail": "文件过大：MCP 上传上限（归档 500MB）。"}, status_code=413)

    try:
        status, body = await backend.put_upload_direct(
            ticket, request.stream(),
        )
    except backend.ToolBackendError as exc:
        await _finish_upload_access(
            ticket, success=False, error_code="upload_backend_failed")
        return JSONResponse({"detail": str(exc)}, status_code=502)
    if status != 200:
        await _finish_upload_access(
            ticket, success=False, error_code="upload_failed")
        detail = body.get("detail") if isinstance(body, dict) else None
        return JSONResponse(
            {"detail": detail or "上传失败，请重取上传地址重试。"},
            status_code=status,
        )
    await _finish_upload_access(ticket, success=True, result=body)
    return JSONResponse(body)


async def _finish_upload_access(
    ticket: str, *, success: bool, error_code: str | None = None,
    result: dict | None = None,
) -> None:
    payload = complete_upload_ticket(
        ticket, success=success, error_code=error_code, result=result)
    if payload is None:
        return
    await _safe_post_access_record(payload, phase="upload_complete")


__all__ = ["mcp", "__version__"]
