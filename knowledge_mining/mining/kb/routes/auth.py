"""认证与用户管理路由。

- POST /api/kb/auth/verify：内部端点（main_control 调，X-Internal-Auth 校验），验密码返回用户。
- GET/POST/PATCH /api/kb/users：admin 用户管理（require_admin）。
- POST /api/kb/users/{id}/reset-password：admin 重置密码。
- POST /api/kb/users/me/password：任一登录用户改自己密码。

所有 /api/kb/* 路由（含本文件的用户管理）经 current_user 校验 X-KB-User + X-Internal-Auth。
verify 例外：它是登录语义（main_control 的服务端调用），不挂 current_user，独立校验 X-Internal-Auth。
"""
from __future__ import annotations

from hmac import compare_digest
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
    password: str | None = None


class IdentifyReq(BaseModel):
    username: str


class CreateUserReq(BaseModel):
    username: str
    password: str | None = None  # admin 必填；member（工号）无密码
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
    """内部端点（identify/verify/reload-auth-config）的 X-Internal-Auth 校验。

    以路由级 dependency 挂载：在 body 校验（422）之前执行，缺失/不符一律 401，
    不向未鉴权调用方泄露参数 schema。与 current_user 同语义。
    """
    secret = get_internal_verify_secret()
    if not secret:
        raise HTTPException(401, "auth not initialized")
    if not compare_digest(request.headers.get("X-Internal-Auth", ""), secret):
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

@router.post("/auth/identify", dependencies=[Depends(_require_internal)])
async def identify(
    body: IdentifyReq,
    svc: UserService = Depends(get_user_service),
) -> dict[str, Any]:
    """登录第一步：按用户名判定模式（password / member / not_found）。内部端点。"""
    return await svc.identify(body.username)


@router.post("/auth/verify", dependencies=[Depends(_require_internal)])
async def verify_credentials(
    body: VerifyReq,
    svc: UserService = Depends(get_user_service),
) -> dict[str, Any]:
    user = await svc.verify_credentials(username=body.username, password=body.password)
    if user is None:
        raise HTTPException(401, "invalid credentials")
    return {"ok": True, "user": {
        "username": user["username"],
        "display_name": user.get("display_name"),
        "site_role": user["site_role"],
    }}


@router.post("/admin/reload-auth-config", dependencies=[Depends(_require_internal)])
async def reload_auth_config() -> dict[str, Any]:
    """内部端点：强制重拉控制面 auth.yaml 刷本地缓存。

    main_control 的 reload-auth 在更新网关侧 auth.yaml 后扇出调用本端点。否则网关
    注入新 internal_verify_secret、mining 仍验旧值 → 全部代理 401（mining 的 auth
    缓存启动期拉取，原本无 reload 通路）。
    """
    from knowledge_mining.mining.infra import control_plane
    control_plane.fetch_auth_config(force=True)
    return {
        "ok": True,
        "internal_verify_secret_present": bool(control_plane.get_internal_verify_secret()),
    }


class McpKeyVerifyReq(BaseModel):
    """MCP 接入密钥校验（mcp_server 调；X-Internal-Auth 防线）。"""
    key: str


@router.post("/auth/mcp-key-verify", dependencies=[Depends(_require_internal)])
async def verify_mcp_key(
    body: McpKeyVerifyReq,
    request: Request,
) -> dict[str, Any]:
    """验钥 → 绑定身份与开放库。miss（无钥/已轮换/已吊销）→ 401。

    返回 username 与 open_kb_ids：MCP 免二次查询；开放库 ∩ 实时权限由检索层
    authorize 兜底（开放了但权限被收窄的库在检索时自然 403/剔除）。
    """
    from knowledge_mining.mining.kb.deps import get_kb_db
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        McpAccessService,
    )

    svc = McpAccessService(await get_kb_db(request))
    result = await svc.verify_key(body.key)
    if result is None:
        raise HTTPException(401, "invalid mcp key")
    return {
        "ok": True,
        "username": result["username"],
        "user_id": result["user_id"],
        "open_kb_ids": result["open_kb_ids"],
        # kb_names → id 的解析源：开放库 id+name（软删库自动从清单消失）
        "open_kbs": result.get("open_kbs", []),
    }


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
    request: Request,
    admin: dict = Depends(require_admin),
    svc: UserService = Depends(get_user_service),
) -> dict[str, Any]:
    try:
        updated = await svc.update_user(
            user_id=user_id, actor_id=admin["id"],
            display_name=body.display_name, site_role=body.site_role, status=body.status,
        )
        # 禁用/改名即时生效（批次3）：主动失效身份缓存（TTL 只是兜底）
        cache = getattr(request.app.state, "identity_cache", None)
        if cache is not None and updated:
            cache.invalidate(updated["username"])
        return updated
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
