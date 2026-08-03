"""FastMCP server — two tools, instructions carry full usage guide."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from mcp_server import __version__
from mcp_server.client import health_check as _health_check
from mcp_server.client import search_knowledge as _search_knowledge
from mcp_server.schemas import (
    EntityRef,
    HealthResult,
    SearchInput,
)

mcp = FastMCP(
    "multi-domain-knowledge",
    instructions="""\
你是多领域知识证据检索服务。

使用 search_knowledge 检索指定知识域中的证据。每次调用都必须显式指定 domain，
不得使用隐式默认领域，也不得根据问题内容擅自猜测领域。如果无法确定 domain，
先要求调用者明确选择知识域。

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
