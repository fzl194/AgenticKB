"""GET /api/kb/users/me/mcp-keys/tool-meta——MCP 工具默认文案与参数 schema。

钉三件事：
1. 形状：默认提示词 + 三件套工具，每个带 description 与 parameters（JSON Schema，
   properties 内含参数说明——fastmcp 从 docstring Args 段注入）；
2. 数据源是 mcp_server 的 FastMCP 实例（同源零漂移），懒导入进程内缓存；
3. 路由挂载可达且需要登录态（匿名 401 由全局中间件负责，这里只验登录可达）。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_mining.mining.kb.routes import mcp_keys
from knowledge_mining.mining.kb.routes.mcp_keys import _tool_meta


@pytest.fixture(scope="module")
def meta() -> dict:
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_tool_meta())


def test_meta_shape_three_tools_with_param_docs(meta: dict) -> None:
    assert meta["instructions"], "默认提示词不能为空"
    names = [t["name"] for t in meta["tools"]]
    assert set(names) == {"search_knowledge", "get_knowledge", "upload_document"}

    by_name = {t["name"]: t for t in meta["tools"]}
    # 默认描述与参数说明都在（fastmcp：描述=docstring 正文；参数说明在 schema properties）
    for name, tool in by_name.items():
        assert tool["description"], f"{name} 缺默认描述"
        props = tool["parameters"].get("properties", {})
        assert props, f"{name} 缺参数 schema"
        assert all("description" in p for p in props.values()), \
            f"{name} 参数缺 description（docstring Args 段没被 fastmcp 解析？）"

    upload = by_name["upload_document"]
    assert set(upload["parameters"].get("required") or []) == {"kb_name", "filenames"}


def test_meta_cached_within_process(meta: dict) -> None:
    again = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_tool_meta())
    assert again is meta, "进程内应命中同一份缓存"


def test_route_mounted_and_requires_identity() -> None:
    app = FastAPI()
    app.include_router(mcp_keys.router)
    app.dependency_overrides[mcp_keys.current_user] = lambda: {"id": "u-1", "site_role": "member"}

    client = TestClient(app)
    try:
        resp = client.get("/api/kb/users/me/mcp-keys/tool-meta")
        assert resp.status_code == 200
        body = resp.json()
        assert body["instructions"]
        assert len(body["tools"]) == 3
    finally:
        client.close()
        app.dependency_overrides.clear()
