"""KB CRUD + members routes — /api/kb.

Auth: X-KB-User header (Phase 1)。Permissions via KbService.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from knowledge_mining.mining.kb.auth import current_user, require_admin
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.deps import (
    get_kb_db, get_kb_service, get_purge_service,
)
from knowledge_mining.mining.kb.services.kb_service import (
    Duplicate, Forbidden, InvalidDomain, InvalidName, InvalidVisibility,
    KbService, NotFound,
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
    #: 阶段 A：库级默认检索范式（serving operator_paradigm id）。null = 清除，
    #: 回落领域默认。格式校验在此，范式可用性由 resolve 运行时降级兜底。
    default_paradigm_id: str | None = Field(default=None, min_length=1, max_length=64)


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
    if isinstance(exc, InvalidVisibility):
        return HTTPException(400, f"invalid visibility: {exc}")
    if isinstance(exc, InvalidName):
        return HTTPException(400, str(exc))
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
    except (Duplicate, InvalidDomain, InvalidName, InvalidVisibility) as exc:
        raise _map_error(exc) from None


@router.get("")
async def list_kbs(
    domain: str = Query(...),
    include_deleted: bool = Query(False),
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
    kbdb: KbDB = Depends(get_kb_db),
):
    try:
        if include_deleted:
            # 已删库清单（site-admin 专属）：软删时代的存量善后入口
            if user.get("site_role") != "admin":
                raise HTTPException(403, "site_admin_required")
            return await kbdb.list_deleted_kbs(domain=domain)
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
    except (
        NotFound, Forbidden, Duplicate, InvalidDomain, InvalidName,
        InvalidVisibility,
    ) as exc:
        raise _map_error(exc) from None


class KbDeleteConfirm(BaseModel):
    """整库硬删确认（2026-09-16 删除体系：输入库全名，GitHub 风格门槛）."""
    confirm_name: str


@router.delete("/{kb_id}")
async def delete_kb(
    kb_id: str,
    body: KbDeleteConfirm | None = None,
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
    kbdb: KbDB = Depends(get_kb_db),
    purge: Any = Depends(get_purge_service),
):
    """整库硬删（软删退役）：全套文档/挖掘历史/快照独占回收/对象回收。

    权限沿用生命周期管理（owner/site-admin）；软删态库也接受——存量善后。
    """
    from knowledge_mining.mining.kb.services.purge_service import PurgeError
    kb = await kbdb.get_kb(kb_id, include_deleted=True)
    if kb is None or not await kbdb.can_restore(kb_id=kb_id, user_id=user["id"]):
        # 不向非属主泄露已删私有库的存在
        raise HTTPException(404, f"KB {kb_id} not found")
    confirm = (body.confirm_name if body else "") or ""
    if confirm != kb.get("name"):
        raise HTTPException(422, "confirm_name_mismatch: 需输入库全名确认")
    try:
        summary = await purge.purge_kb(kb_id)
    except PurgeError as exc:
        raise HTTPException(409, str(exc)) from exc
    import logging
    logging.getLogger("kb.purge").info(
        "[purge] kb=%s actor=%s docs=%s snapshots=%s objects=%s",
        kb_id, user.get("id"), len(summary.get("deleted_documents") or []),
        summary.get("reclaimed_snapshots"), summary.get("reclaimed_objects"))
    return {"ok": True, **{k: v for k, v in summary.items()
                           if k != "deleted_documents"},
            "deleted_documents": len(summary.get("deleted_documents") or [])}


@router.post("/{kb_id}/restore")
async def restore_kb(
    kb_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: KbService = Depends(get_kb_service),
):
    try:
        return await svc.restore_kb(kb_id=kb_id, actor_id=user["id"])
    except (NotFound, Forbidden, Duplicate) as exc:
        raise _map_error(exc) from None


@router.get("/{kb_id}/quality")
async def get_kb_quality(
    kb_id: str,
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
):
    """A4 质量报告（34 号 P1-1）：结构/表格完整度 + 来源定位覆盖 + 不可查原因.

    维护者视图——current 快照口径（与 readiness 一致）的只读聚合，
    无 DDL、无写路径；作为重挖决策与 A4 评测（来源可解析率分母）的输入。
    """
    if not await kbdb.is_visible(kb_id=kb_id, user_id=user["id"]):
        raise HTTPException(404, f"KB {kb_id} not found")
    return await kbdb.get_kb_quality(kb_id)


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
    except (NotFound, Forbidden, InvalidVisibility) as exc:
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


@router.get("/{kb_id}/members/candidates")
async def list_member_candidates(
    kb_id: str,
    q: str | None = Query(default=None, description="可选:username 前缀过滤"),
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
):
    """可加入该 KB 的候选用户(成员面板选择器用)。

    需对该 KB 的写权限(管理成员 = 写动作):看不到 → 404(不泄露存在性),
    看得到但不能写 → 403。返回 id/username/display_name 最小集,不含敏感字段。
    admin 经 can_write 内的 EXISTS 短路放行。
    """
    if not await kbdb.is_visible(kb_id=kb_id, user_id=user["id"]):
        raise HTTPException(404, f"KB {kb_id} not found")
    if not await kbdb.can_write(kb_id=kb_id, user_id=user["id"]):
        raise HTTPException(403, f"write access required for KB {kb_id}")
    return await kbdb.list_member_candidates(kb_id=kb_id, q=q)


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


# ---------------------------------------------------------------- 快照 GC

@router.post("/snapshots/gc")
async def snapshot_gc(
    user: dict[str, Any] = Depends(require_admin),
    purge: Any = Depends(get_purge_service),
):
    """快照废弃轨道手动触发（每日后台自动跑；管理员可强制）."""
    marked = await purge.deprecate_superseded_snapshots()
    reclaimed = await purge.reclaim_deprecated_snapshots()
    return {**marked, **reclaimed}
