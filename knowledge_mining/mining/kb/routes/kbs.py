"""KB CRUD + members routes — /api/kb.

Auth: X-KB-User header (Phase 1)。Permissions via KbService.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.deps import get_kb_db, get_kb_service
from knowledge_mining.mining.kb.services.kb_service import (
    Duplicate, Forbidden, InvalidDomain, KbService, NotFound,
)

router = APIRouter(prefix="/api/kb", tags=["kb"])


# ----------------------------------------------------------------- models

class KbCreate(BaseModel):
    domain: str
    name: str
    visibility: str = "private"
    description: str | None = None


class KbUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: str | None = None
    mining_workflow_id: str | None = None


class MemberAdd(BaseModel):
    username: str = Field(description="登录名 / X-KB-User 用户名")
    role: str = "viewer"


# ----------------------------------------------------------------- helpers

def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(404, str(exc) or "not found")
    if isinstance(exc, Forbidden):
        return HTTPException(403, str(exc) or "forbidden")
    if isinstance(exc, Duplicate):
        return HTTPException(409, str(exc))
    if isinstance(exc, InvalidDomain):
        return HTTPException(400, f"invalid domain: {exc}")
    return HTTPException(500, str(exc))


# ----------------------------------------------------------------- KB CRUD

@router.post("", status_code=201)
async def create_kb(
    body: KbCreate,
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
):
    try:
        return await svc.create_kb(
            domain=body.domain, name=body.name, owner_id=user["id"],
            visibility=body.visibility, description=body.description,
        )
    except (Duplicate, InvalidDomain) as exc:
        raise _map_error(exc) from None


@router.get("")
async def list_kbs(
    domain: str = Query(...),
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
):
    try:
        return await svc.list_visible(user_id=user["id"], domain=domain)
    except InvalidDomain as exc:
        raise _map_error(exc) from None


@router.get("/{kb_id}")
async def get_kb(
    kb_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
):
    try:
        return await svc.get_kb(kb_id=kb_id, user_id=user["id"])
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.patch("/{kb_id}")
async def update_kb(
    kb_id: str,
    body: KbUpdate,
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
):
    # model_fields_set 区分「未传」与「显式置 null」：未传的列不动，显式 null → 清空（SET NULL）。
    fields = body.model_dump(exclude_unset=True)
    try:
        return await svc.update_kb(kb_id=kb_id, actor_id=user["id"], fields=fields)
    except (NotFound, Forbidden, InvalidDomain) as exc:
        raise _map_error(exc) from None


@router.delete("/{kb_id}")
async def delete_kb(
    kb_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
):
    try:
        return await svc.soft_delete(kb_id=kb_id, actor_id=user["id"])
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.get("/{kb_id}/runs")
async def list_kb_runs(
    kb_id: str,
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
):
    """本 KB 的挖掘记录（KB「挖掘」tab 用，最新在前）。"""
    if not await kbdb.is_visible(kb_id=kb_id, user_id=user["id"]):
        raise HTTPException(404, f"KB {kb_id} not found")
    return await kbdb.list_kb_runs(kb_id)


# ----------------------------------------------------------------- members

@router.post("/{kb_id}/members", status_code=201)
async def add_member(
    kb_id: str,
    body: MemberAdd,
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
):
    try:
        return await svc.add_member(
            kb_id=kb_id, actor_id=user["id"], username=body.username, role=body.role,
        )
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.get("/{kb_id}/members")
async def list_members(
    kb_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
):
    try:
        return await svc.list_members(kb_id=kb_id, user_id=user["id"])
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.delete("/{kb_id}/members/{user_id}")
async def remove_member(
    kb_id: str,
    user_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
):
    try:
        await svc.remove_member(kb_id=kb_id, actor_id=user["id"], user_id=user_id)
        return {"ok": True}
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None
