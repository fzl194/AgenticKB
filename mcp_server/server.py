"""FastMCP server — tool definitions; instructions carry the full usage guide."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from mcp_server import __version__
from mcp_server.client import get_segment_fulltext as _get_segment_fulltext
from mcp_server.client import health_check as _health_check
from mcp_server.client import search_knowledge as _search_knowledge
from mcp_server.schemas import (
    EntityRef,
    FullTextInput,
    HealthResult,
    SearchInput,
    SegmentRef,
)

mcp = FastMCP(
    "multi-domain-knowledge",
    instructions="""\
你是多领域知识证据检索服务。

使用 search_knowledge 检索指定知识域中的证据。每次调用都必须显式指定 domain，
不得使用隐式默认领域，也不得根据问题内容擅自猜测领域。如果无法确定 domain，
先要求调用者明确选择知识域。

search_knowledge 返回的证据文本是压缩过的。需要准确引用条款、参数或步骤原文时，
用 get_segment_fulltext 取回完整版本再作答——不要依据被截断的文本推测缺失内容。

回答时应区分证据直接支持的内容、基于证据的推断，以及当前缺失或不确定的信息；
不得编造命令、参数、约束、依赖或步骤。
""",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "9000")),
)


# ── Tools ────────────────────────────────────────────────────────────────


# health_check 暂不对外暴露，内部可通过 _health_check() 调用
# @mcp.tool()
# def health_check() -> HealthResult:
#     """检查知识库是否可用。不可用时不要编造知识，告知用户当前无法查询。"""
#     return _health_check()


@mcp.tool()
def search_knowledge(
    query: str,
    domain: str,
    scope: dict | None = None,
    entities: list[dict] | None = None,
    debug: bool = False,
) -> dict:
    """按指定知识域检索知识证据。

    检索策略由该知识域绑定的检索范式决定；未绑定的域走默认检索管线。返回结果里的
    `_retrieval` 字段说明本次由哪条引擎作答。

    Args:
        query: 用户原问题。
        domain: 必填知识域标识，例如 civil_engineering 或 odn。
        scope: 产品、对象或场景等附加约束。**当前不影响检索结果**：后端检索管线不消费
            该字段，传入只会在 `_retrieval.ignored_args` 中回报。
        entities: 已识别实体列表，每项包含 name，可选 type/normalized_name。
            **当前同样不影响检索结果**——实体由后端查询理解自行抽取。
        debug: 是否返回检索过程诊断信息。
    """
    entity_refs = [EntityRef(**e) for e in entities] if entities else None

    inp = SearchInput(
        query=query,
        domain=domain,
        scope=scope,
        entities=entity_refs,
        debug=debug,
    )
    return _search_knowledge(inp)


@mcp.tool()
def get_segment_fulltext(
    domain: str,
    refs: list[dict],
    granularity: str = "segment",
    window_radius: int = 1,
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

    Returns:
        `items` 与 refs 一一对应。`found=false` 表示该 id 已不在当前可检索范围内
        （内容被重新挖掘或该库不可见），此时应基于已有证据作答并说明这一点，不要重试。
        `segments` 按原文顺序排列，`role` 为 `target` 的是命中片段，`before`/`after`
        是上下文。`segments[].documentName` / `kbId` 可用于标注出处。
    """
    inp = FullTextInput(
        domain=domain,
        refs=[SegmentRef(**r) for r in refs],
        granularity=granularity,
        window_radius=window_radius,
    )
    return _get_segment_fulltext(inp)
