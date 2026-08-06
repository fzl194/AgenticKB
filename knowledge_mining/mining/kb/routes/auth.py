"""认证与用户管理路由。

- POST /api/kb/auth/verify：内部端点（main_control 调，X-Internal-Auth 校验），验密码返回用户。
- GET/POST/PATCH /api/kb/users：admin 用户管理（require_admin）。
- POST /api/kb/users/{id}/reset-password：admin 重置密码。
- POST /api/kb/users/me/password：任一登录用户改自己密码。

所有 /api/kb/* 路由（含本文件的用户管理）经 current_user 校验 X-KB-User + X-Internal-Auth。
verify 例外：它是登录语义（main_control 的服务端调用），不挂 current_user，独立校验 X-Internal-Auth。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from knowledge_mining.mining.infra.control_plane import get_internal_verify_secret
from knowledge_mining.mining.kb.auth import current_user, require_admin
from knowledge_mining.mining.kb.deps import get_user_service
from knowledge_mining.mining.kb.services.user_service import (
    DuplicateUser, InvalidRole, UserError, UserNotFound, UserService, WrongPassword,
)

router = APIRouter(prefix="/api/kb", tags=["kb-auth"])


# ---------------------------------------------------------------- models

class VerifyReq(BaseModel):
    username: str
    password: str


class CreateUserReq(BaseModel):
    username: str
    password: str
    site_role: str = "member"
    display_name: str | None = None


class UpdateUserReq(BaseModel):
    display_name: str | None = None
    site_role: str | None = None
    status: str | None = None


class ResetPasswordReq(BaseModel):
    password: str


class ChangeMyPasswordReq(BaseModel):
    old: str
    new: str


# ---------------------------------------------------------------- helpers

def _require_internal(request: Request) -> None:
    """verify 端点独立校验 X-Internal-Auth（与 current_user 同语义：缺失/不符 → 401）。"""
    secret = get_internal_verify_secret()
    if not secret:
        raise HTTPException(401, "auth not initialized")
    if request.headers.get("X-Internal-Auth", "") != secret:
        raise HTTPException(401, "unauthenticated")


def _map_user_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UserNotFound):
        return HTTPException(404, str(exc) or "user not found")
    if isinstance(exc, DuplicateUser):
        return HTTPException(409, str(exc) or "duplicate user")
    if isinstance(exc, (InvalidRole, WrongPassword, UserError)):
        return HTTPException(400, str(exc))
    return HTTPException(500, str(exc))


# ---------------------------------------------------------------- verify (internal)

@router.post("/auth/verify")
async def verify_credentials(
    body: VerifyReq, request: Request,
    svc: UserService = Depends(get_user_service),
) -> dict[str, Any]:
    _require_internal(request)
    user = await svc.verify_credentials(username=body.username, password=body.password)
    if user is None:
        raise HTTPException(401, "invalid credentials")
    return {"ok": True, "user": {
        "username": user["username"],
        "display_name": user.get("display_name"),
        "site_role": user["site_role"],
    }}


# ---------------------------------------------------------------- user CRUD (admin)

@router.get("/users")
async def list_users(
    _admin: dict = Depends(require_admin),
    svc: UserService = Depends(get_user_service),
) -> list[dict[str, Any]]:
    return await svc.list_users()


@router.post("/users", status_code=201)
async def create_user(
    body: CreateUserReq,
    _admin: dict = Depends(require_admin),
    svc: UserService = Depends(get_user_service),
) -> dict[str, Any]:
    try:
        return await svc.create_user(
            username=body.username, password=body.password,
            site_role=body.site_role, display_name=body.display_name,
        )
    except (DuplicateUser, InvalidRole, UserError) as exc:
        raise _map_user_error(exc) from None


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str, body: UpdateUserReq,
    _admin: dict = Depends(require_admin),
    svc: UserService = Depends(get_user_service),
) -> dict[str, Any]:
    try:
        return await svc.update_user(
            user_id=user_id, display_name=body.display_name,
            site_role=body.site_role, status=body.status,
        )
    except (UserNotFound, InvalidRole, UserError) as exc:
        raise _map_user_error(exc) from None


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str, body: ResetPasswordReq,
    _admin: dict = Depends(require_admin),
    svc: UserService = Depends(get_user_service),
) -> dict[str, Any]:
    try:
        await svc.reset_password(user_id, body.password)
        return {"ok": True}
    except (UserNotFound, UserError) as exc:
        raise _map_user_error(exc) from None


@router.post("/users/me/password")
async def change_my_password(
    body: ChangeMyPasswordReq,
    user: dict = Depends(current_user),
    svc: UserService = Depends(get_user_service),
) -> dict[str, Any]:
    try:
        await svc.change_own_password(user_id=user["id"], old=body.old, new=body.new)
        return {"ok": True}
    except (UserNotFound, WrongPassword, UserError) as exc:
        raise _map_user_error(exc) from None
