"""on_initialize 按会话注入自定义提示词。

fastmcp 3.4.7 的 InitializeResult 在 call_next 内部由 SDK 组装并直接发往写流，
中间件拿到的是发送后的捕获对象，事后改写无效；改 FastMCP 实例属性又会串到
其他连接。唯一生效路径是 call_next 之前改写**本连接** ServerSession.
_init_options.instructions（SDK 组装响应时读取）。这里用假 session 钉住这一
行为——升级 fastmcp / mcp SDK 后跑本文件即知私有属性路径是否仍成立。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from mcp.server.models import InitializationOptions
from mcp.types import ServerCapabilities

from mcp_server import server as srv
from mcp_server.identity import Identity


def _fake_context(instructions_default: str | None):
    options = InitializationOptions(
        server_name="test",
        server_version="0",
        capabilities=ServerCapabilities(),
        instructions=instructions_default,
    )
    context = SimpleNamespace(
        fastmcp_context=SimpleNamespace(session=SimpleNamespace(_init_options=options)),
    )
    return context, options


@pytest.mark.asyncio
async def test_custom_instructions_overrides_session_default(monkeypatch) -> None:
    context, options = _fake_context("默认提示词")
    ident = Identity(
        username="alice", user_id="u-1", open_kbs=(), instructions="运维专用的定制提示词",
    )
    monkeypatch.setattr(srv, "_identity_or_none", lambda: ident)

    async def call_next(_ctx):
        # 断言时机必须在 call_next 之前完成改写——SDK 在这里面组装响应
        assert options.instructions == "运维专用的定制提示词"
        return object()

    await srv.PersonalizationMiddleware().on_initialize(context, call_next)


@pytest.mark.asyncio
async def test_no_identity_keeps_default_instructions(monkeypatch) -> None:
    context, options = _fake_context("默认提示词")
    monkeypatch.setattr(srv, "_identity_or_none", lambda: None)

    async def call_next(_ctx):
        return object()

    await srv.PersonalizationMiddleware().on_initialize(context, call_next)
    assert options.instructions == "默认提示词"


@pytest.mark.asyncio
async def test_identity_without_custom_instructions_keeps_default(monkeypatch) -> None:
    """钥匙没配提示词（instructions=None）时下发服务器默认，不写空串。"""
    context, options = _fake_context("默认提示词")
    ident = Identity(username="bob", user_id="u-2", open_kbs=())
    monkeypatch.setattr(srv, "_identity_or_none", lambda: ident)

    async def call_next(_ctx):
        return object()

    await srv.PersonalizationMiddleware().on_initialize(context, call_next)
    assert options.instructions == "默认提示词"


def test_fastmcp_context_session_reachable_during_initialize() -> None:
    """私有链路的第一环：真实 fastmcp Context 在 init 阶段（无 request_context）
    必须能经 _session 回退拿到 session。上面的用例只钉了第二环（_init_options
    在 call_next 内被读取）；fastmcp 升级若拿掉这一回退，本测试先红。"""
    from fastmcp import FastMCP
    from fastmcp.server.context import Context

    session = SimpleNamespace(_init_options=None)
    # Context 对 fastmcp 建 weakref，不能传 None；空实例足够轻
    context = Context(fastmcp=FastMCP("test"), session=session)
    assert context.session is session
