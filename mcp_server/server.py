"""FastMCP server — tool definitions; instructions carry the full usage guide.

阶段 A（批次5）：强制用户密钥鉴权（无钥/错钥一律拒绝，无匿名模式）；检索以库为中心
——开放 1 个库时连库名都不用传，范式跟随库绑定自动走。
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import Context, FastMCP

from mcp_server import __version__
from mcp_server.client import get_segment_fulltext as _get_segment_fulltext
from mcp_server.client import health_check as _health_check
from mcp_server.client import search_knowledge as _search_knowledge
from mcp_server.identity import IdentityError, require_identity, resolve_kb_ids
from mcp_server.schemas import (
    FullTextInput,
    SearchInput,
    SegmentRef,
)

mcp = FastMCP(
    "multi-domain-knowledge",
    instructions="""\
你是多领域知识证据检索服务（用户级接入：调用必须携带 Bearer 密钥）。

使用 search_knowledge 检索指定知识域中的证据。每次调用都必须显式指定 domain，
不得使用隐式默认领域，也不得根据问题内容擅自猜测领域。如果无法确定 domain，
先要求调用者明确选择知识域。

知识库范围由密钥主人配置（开放哪些库）：只开放一个库时不必传 kb_names，直接检索
即走该库及其绑定的检索范式；开放多个库时可传 kb_names 缩小范围，不传则检索全部
开放库。检索范式不需要指定——它跟随知识库绑定自动选择。

search_knowledge 返回的证据文本是压缩过的。需要准确引用条款、参数或步骤原文时，
用 get_segment_fulltext 取回完整版本再作答——不要依据被截断的文本推测缺失内容。
取原文时把上次结果里的 `_retrieval.paradigm_id` 原样传回去，否则会在另一套语料里
查这些 id，一条都找不到。

回答时应区分证据直接支持的内容、基于证据的推断，以及当前缺失或不确定的信息；
不得编造命令、参数、约束、依赖或步骤。
""",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "9000")),
)


def _identity(ctx: Context):
    """强制鉴权：取不到请求头（非 HTTP 传输）与无钥/错钥同样拒绝。"""
    try:
        headers = ctx.request_context.request.headers
    except Exception:
        headers = None
    if headers is None:
        raise IdentityError("MCP 接入仅支持 HTTP 传输，且必须携带 Bearer 密钥。")
    return require_identity(headers)


# ── Tools ────────────────────────────────────────────────────────────────


# health_check 暂不对外暴露，内部可通过 _health_check() 调用
# @mcp.tool()
# def health_check() -> HealthResult:
#     """检查知识库是否可用。不可用时不要编造知识，告知用户当前无法查询。"""
#     return _health_check()


@mcp.tool()
def search_knowledge(
    ctx: Context,
    query: str,
    domain: str,
    kb_names: list[str] | None = None,
    paradigm: str | None = None,
    debug: bool = False,
) -> dict:
    """按指定知识域检索知识证据（以密钥主人的开放库为范围）。

    检索范围与管线都是自动的：范围 = 密钥主人开放的库（只开放一个库时不传 kb_names
    即可）；管线 = 目标库绑定的检索范式（未绑定时按 领域默认 → 官方默认 兜底）。
    返回结果里的 `_retrieval` 说明本次由哪条管线、以何种方式选中（selected_by），库级
    绑定不可用时会有 degraded 留痕。

    Args:
        query: 用户原问题。
        domain: 必填知识域标识，例如 civil_engineering 或 odn。
        kb_names: 可选，知识库名称列表，用于在密钥主人开放的多个库中缩小范围。
            不传 = 检索全部开放库；传了未开放的库名会直接报错并列出当前开放清单。
            密钥主人只开放了一个库时无需传。
        paradigm: 一般不需要传——范式跟随知识库绑定自动选择。仅在明确要用同域下另一条
            已发布范式时按名指定（取 `_retrieval.available_paradigms` 里的 name）；
            指定不存在的范式会报错并列出可选项，不会静默改用别的范式。
        debug: 是否返回检索过程诊断信息。
    """
    identity = _identity(ctx)
    kb_ids = resolve_kb_ids(identity, kb_names)

    inp = SearchInput(
        query=query,
        domain=domain,
        paradigm=paradigm,
        scope=None,
        entities=None,
        debug=debug,
    )
    return _search_knowledge(inp, identity, kb_ids)


@mcp.tool()
def get_segment_fulltext(
    ctx: Context,
    domain: str,
    refs: list[dict],
    granularity: str = "segment",
    window_radius: int = 1,
    paradigm_id: str | None = None,
) -> dict:
    """取回 search_knowledge 结果中某几条证据的**完整原文**。

    search_knowledge 返回的 `text` 是按上下文预算压缩过的：命中项被硬截断（末尾常见
    `...`），其余项只保留与问题最相关的句子。**当你需要引用条款、参数、步骤的准确原文，
    或看到文本被截断时，用这个工具取回未压缩的版本再作答，不要根据残文推测缺失内容。**

    Args:
        domain: 与产生这些证据的那次 search_knowledge 相同的知识域。必须一致——
            不同知识域对应不同语料范围，跨域查会一条都找不到。
        refs: 要展开的条目，每项 `{"type": ..., "id": ...}`。直接取自 search_knowledge
            结果：`type` 用条目的 `kind`（命中项是 `retrieval_unit`，上下文/支撑项是
            `raw_segment`），`id` 用条目的 `id`。单次最多 50 条。
        granularity: `segment` 只返回该片段本身；`window` 额外带回前后相邻片段。
            **当原文在片段开头或结尾处语义不完整（条件、例外、后续步骤像是被切断）时，
            用 `window` 再取一次**——切分边界常把一句话或一个条款劈成两段。
        window_radius: `window` 模式下前后各取几段，1-5，默认 1。
        paradigm_id: **把产生这些证据的那次 search_knowledge 结果里
            `_retrieval.paradigm_id` 的值原样传回来。** 不同范式对应不同的语料范围，
            用错范围会把全部条目报成 `found=false`（看起来像内容被重挖，实际是查错了
            地方）。省略则按该知识域的默认范式查——仅当那次检索也没指定范式时
            才是对的。

    Returns:
        `items` 与 refs 一一对应。`found=false` 表示该 id 已不在当前可检索范围内
        （内容被重新挖掘或该库不可见），此时应基于已有证据作答并说明这一点，不要重试。
        `segments` 按原文顺序排列，`role` 为 `target` 的是命中片段，`before`/`after`
        是上下文。`segments[].documentName` / `kbId` 可用于标注出处。
    """
    identity = _identity(ctx)
    inp = FullTextInput(
        domain=domain,
        refs=[SegmentRef(**r) for r in refs],
        granularity=granularity,
        window_radius=window_radius,
        paradigm_id=paradigm_id,
    )
    return _get_segment_fulltext(inp, identity)
