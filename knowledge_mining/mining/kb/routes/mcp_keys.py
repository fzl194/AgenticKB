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
    McpKeyError,
    McpKeyService,
)

router = APIRouter(prefix="/api/kb/users/me/mcp-keys", tags=["kb-mcp-keys"])


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
    if isinstance(exc, (KeyLimitExceeded, KeyNameConflict, KeyRevoked)):
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
