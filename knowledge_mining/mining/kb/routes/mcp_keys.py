"""用户级 MCP 钥匙路由族（51号批次2 / 多钥匙替代旧 mcp-access 路由）。

- GET  /api/kb/users/me/mcp-keys                本人钥匙列表（含 domain_bound）
- POST /api/kb/users/me/mcp-keys                新建钥匙（明文仅此一次返回）
- POST /api/kb/users/me/mcp-keys/{key_id}/rotate 轮换（旧钥立即失效）
- POST /api/kb/users/me/mcp-keys/{key_id}/revoke 吊销（幂等）
- PUT  /api/kb/users/me/mcp-keys/{key_id}/open-kbs 全量覆盖开放库
- PUT  /api/kb/users/me/mcp-keys/{key_id}/config  工具开关/提示词/工具描述

注意：本文件路径全是字面量静态段（/users/me/...），必须在 kb_router（动态
/api/kb/{kb_id}）之前注册（app.py 中挂在 kb_auth_router 之后）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.deps import get_kb_db
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.mcp_key_service import (
    KeyDomainNotBound,
    KeyLimitExceeded,
    KeyNameConflict,
    KeyNotFound,
    KeyRevoked,
    KeyMustBeRevoked,
    McpKeyError,
    McpKeyService,
)

router = APIRouter(prefix="/api/kb/users/me/mcp-keys", tags=["kb-mcp-keys"])

logger = logging.getLogger(__name__)


def _get_service(kbdb: KbDB = Depends(get_kb_db)) -> McpKeyService:
    return McpKeyService(kbdb)


def _http_error(exc: McpKeyError) -> HTTPException:
    """异常族 → HTTP 映射。子类判定必须在基类兜底（422）之前。

    403 的响应体形状是硬约定（mcp_server / 前端按 detail.code 分支）：
    {"detail": {"code": "domain_not_bound", "message": <文案>}}。
    新增异常子类不在此映射即落 422 兜底，不会裸 500。
    """
    if isinstance(exc, KeyNotFound):
        return HTTPException(404, "钥匙不存在")
    if isinstance(exc, KeyDomainNotBound):
        return HTTPException(403, {
            "code": "domain_not_bound", "message": str(exc),
        })
    if isinstance(exc, (KeyLimitExceeded, KeyNameConflict, KeyRevoked, KeyMustBeRevoked)):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))  # McpKeyError 基类兜底


# ------------------------------------------------------------ 查询

@router.get("")
async def list_my_keys(
    user: dict[str, Any] = Depends(current_user),
    svc: McpKeyService = Depends(_get_service),
) -> dict[str, Any]:
    keys = await svc.list_keys(
        user_id=user["id"], is_admin=user.get("site_role") == "admin",
    )
    return {"keys": keys}


# ------------------------------------------------------------ 工具默认文案与参数

#: 进程内缓存：工具元数据来自 mcp_server 代码里的 docstring/schema（静态注册面），
#: 只在首次请求时取一次。失效场景只有发版换码，进程重启自然重载。
_TOOL_META_CACHE: dict[str, Any] | None = None


async def _tool_meta() -> dict[str, Any]:
    global _TOOL_META_CACHE
    if _TOOL_META_CACHE is None:
        # 同容器直接 import mcp_server 的 FastMCP 实例：docstring/schema 与线上
        # MCP 服务同源零漂移，不走 HTTP。懒导入避免 mining 启动即背上 mcp 栈
        # （首次导入约 1-2s，会占住事件循环——只发生一次，内网可接受）。
        from mcp_server import server as mcp_server_module

        tools = await mcp_server_module.mcp.list_tools(run_middleware=False)
        _TOOL_META_CACHE = {
            "instructions": mcp_server_module.DEFAULT_INSTRUCTIONS,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.parameters or {},
                }
                for t in tools
                if t.name in mcp_server_module.TOOL_NAMES
            ],
        }
    return _TOOL_META_CACHE


@router.get("/tool-meta")
async def get_tool_meta(
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """MCP 工具默认提示词/默认工具说明/参数 schema。

    给钥匙配置抽屉用：预填默认文案、展示每个工具的参数表。只读静态面，
    不涉及钥匙个性化数据；无 DB 依赖。
    """
    try:
        return await _tool_meta()
    except Exception:
        # 缓存不落，下次请求重试；503 让前端回落到"不预填"而非误当无默认。
        logger.warning("mcp tool-meta 装配失败（mcp_server import/list_tools）",
                       exc_info=True)
        raise HTTPException(503, "工具默认文案暂不可用，请稍后重试") from None


# ------------------------------------------------------------ 生命周期

class CreateKeyBody(BaseModel):
    name: str
    domain: str


@router.post("", status_code=201)
async def create_my_key(
    body: CreateKeyBody,
    user: dict[str, Any] = Depends(current_user),
    svc: McpKeyService = Depends(_get_service),
) -> dict[str, Any]:
    """新建单域钥匙。明文仅本次响应可见。"""
    try:
        return await svc.create_key(
            user_id=user["id"], name=body.name, domain=body.domain,
            is_admin=user.get("site_role") == "admin",
        )
    except McpKeyError as exc:
        raise _http_error(exc) from None


@router.post("/{key_id}/rotate")
async def rotate_my_key(
    key_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: McpKeyService = Depends(_get_service),
) -> dict[str, Any]:
    """轮换：旧钥立即失效；新明文仅本次响应可见。"""
    try:
        return await svc.rotate_key(user_id=user["id"], key_id=key_id)
    except McpKeyError as exc:
        raise _http_error(exc) from None


@router.post("/{key_id}/revoke", status_code=204)
async def revoke_my_key(
    key_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: McpKeyService = Depends(_get_service),
) -> Response:
    """吊销（幂等：已吊销不报错）。"""
    try:
        await svc.revoke_key(user_id=user["id"], key_id=key_id)
    except McpKeyError as exc:
        raise _http_error(exc) from None
    return Response(status_code=204)


@router.delete("/{key_id}", status_code=204)
async def delete_my_key(
    key_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: McpKeyService = Depends(_get_service),
) -> Response:
    """Delete one revoked key from the visible list while retaining its audit row."""
    try:
        await svc.delete_key(user_id=user["id"], key_id=key_id)
    except McpKeyError as exc:
        raise _http_error(exc) from None
    return Response(status_code=204)


# ------------------------------------------------------------ 开放库 / 配置

# PUT body：全量覆盖该钥匙开放库；空数组=清空。
class OpenKbsBody(BaseModel):
    kb_ids: list[str]


@router.put("/{key_id}/open-kbs")
async def put_my_key_open_kbs(
    key_id: str,
    body: OpenKbsBody,
    user: dict[str, Any] = Depends(current_user),
    svc: McpKeyService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        final = await svc.replace_open_kbs(
            user_id=user["id"], key_id=key_id, kb_ids=body.kb_ids,
        )
    except McpKeyError as exc:
        raise _http_error(exc) from None
    return {"open_kb_ids": final}


# 工具开关 / 提示词 / 工具描述。None = 不改；instructions "" = 恢复默认。
class McpConfigBody(BaseModel):
    open_tools: list[str] | None = None
    instructions: str | None = None
    tool_descriptions: dict[str, str] | None = None


@router.put("/{key_id}/config")
async def put_my_key_config(
    key_id: str,
    body: McpConfigBody,
    user: dict[str, Any] = Depends(current_user),
    svc: McpKeyService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return await svc.update_config(
            user_id=user["id"], key_id=key_id,
            open_tools=body.open_tools,
            instructions=body.instructions,
            tool_descriptions=body.tool_descriptions,
        )
    except McpKeyError as exc:
        raise _http_error(exc) from None
