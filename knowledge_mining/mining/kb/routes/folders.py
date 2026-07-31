"""KB 文件夹路由 — /api/kb/{kb_id}/folders。

一等文件夹 CRUD（G2）。rename/move 见 G3（同路由文件的 PATCH / move 端点）。
权限：写操作要求 owner/editor（service 层 _assert_write），不可见 → 404 不泄露。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.deps import get_folder_service
from knowledge_mining.mining.kb.routes.kbs import _map_error
from knowledge_mining.mining.kb.services.folder_service import FolderService
from knowledge_mining.mining.kb.services.kb_service import Duplicate, Forbidden, NotFound

router = APIRouter(prefix="/api/kb/{kb_id}/folders", tags=["kb-folders"])


class FolderCreate(BaseModel):
    parent_id: str | None = None
    name: str


class FolderRename(BaseModel):
    name: str


class FolderMove(BaseModel):
    target_parent_id: str | None = None  # None = 移到根


@router.get("")
async def list_folders(
    kb_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: FolderService = Depends(get_folder_service),
):
    try:
        return await svc.list_folders(kb_id=kb_id, user_id=user["id"])
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.post("", status_code=201)
async def create_folder(
    kb_id: str,
    body: FolderCreate,
    user: dict[str, Any] = Depends(current_user),
    svc: FolderService = Depends(get_folder_service),
):
    try:
        return await svc.create_folder(
            kb_id=kb_id, parent_id=body.parent_id, name=body.name, user_id=user["id"],
        )
    except (NotFound, Forbidden, Duplicate) as exc:
        raise _map_error(exc) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.patch("/{folder_id}")
async def rename_folder(
    kb_id: str,
    folder_id: str,
    body: FolderRename,
    user: dict[str, Any] = Depends(current_user),
    svc: FolderService = Depends(get_folder_service),
):
    try:
        return await svc.rename_folder(folder_id=folder_id, name=body.name, user_id=user["id"])
    except (NotFound, Forbidden, Duplicate) as exc:
        raise _map_error(exc) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/{folder_id}/move")
async def move_folder(
    kb_id: str,
    folder_id: str,
    body: FolderMove,
    user: dict[str, Any] = Depends(current_user),
    svc: FolderService = Depends(get_folder_service),
):
    try:
        return await svc.move_folder(
            folder_id=folder_id, target_parent_id=body.target_parent_id, user_id=user["id"],
        )
    except (NotFound, Forbidden, Duplicate) as exc:
        raise _map_error(exc) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None  # 移入自身/子树 → 400


@router.delete("/{folder_id}")
async def delete_folder(
    kb_id: str,
    folder_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: FolderService = Depends(get_folder_service),
):
    try:
        await svc.delete_folder(folder_id=folder_id, user_id=user["id"])
        return {"ok": True}
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None  # 非空 → 409 Conflict
