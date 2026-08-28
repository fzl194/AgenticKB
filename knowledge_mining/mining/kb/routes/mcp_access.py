"""用户级 MCP 接入路由（阶段 A / 批次5）。

- GET  /api/kb/users/me/mcp-access        本人密钥状态 + 开放库
- POST /api/kb/users/me/mcp-access/rotate 生成/轮换密钥（明文仅此一次返回）
- PUT  /api/kb/users/me/mcp-access/open-kbs 全量覆盖开放库勾选

注意：本文件路径全是字面量静态段，必须在 kb_router（动态 /api/kb/{kb_id}）之前
注册（app.py 中挂在 kb_auth_router 之后）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.deps import get_kb_db
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.mcp_access_service import (
    McpAccessError,
    McpAccessService,
)

router = APIRouter(prefix="/api/kb/users/me/mcp-access", tags=["kb-mcp-access"])


def _get_service(kbdb: KbDB = Depends(get_kb_db)) -> McpAccessService:
    return McpAccessService(kbdb)


@router.get("")
async def get_mcp_access(
    user: dict[str, Any] = Depends(current_user),
    svc: McpAccessService = Depends(_get_service),
) -> dict[str, Any]:
    status = await svc.get_status(user_id=user["id"])
    if status is None:
        return {"configured": False, "open_kb_ids": []}
    return {"configured": True, **status}


@router.post("/rotate")
async def rotate_mcp_key(
    user: dict[str, Any] = Depends(current_user),
    svc: McpAccessService = Depends(_get_service),
) -> dict[str, Any]:
    """生成或轮换。旧钥立即失效；明文仅本次响应可见。"""
    return await svc.rotate_key(user_id=user["id"])


# PUT body：全量覆盖开放库；空数组=清空。
class OpenKbsBody(BaseModel):
    kb_ids: list[str]


@router.put("/open-kbs")
async def put_open_kbs(
    body: OpenKbsBody,
    user: dict[str, Any] = Depends(current_user),
    svc: McpAccessService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        final = await svc.replace_open_kbs(user_id=user["id"], kb_ids=body.kb_ids)
    except McpAccessError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"open_kb_ids": final}
