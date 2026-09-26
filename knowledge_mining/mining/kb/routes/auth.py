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
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from knowledge_mining.mining.infra.control_plane import get_internal_verify_secret
from knowledge_mining.mining.infra.domain_pack import resolve_domain
from knowledge_mining.mining.kb.auth import current_user, require_admin
from knowledge_mining.mining.kb.db import DomainMembershipConflict, KbDB
from knowledge_mining.mining.kb.deps import get_kb_db, get_user_service

import logging

logger = logging.getLogger(__name__)
from knowledge_mining.mining.kb.services.mcp_key_service import (
    McpKeyService,
    normalize_legacy_open_tools,
)
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


class UserDomainsReq(BaseModel):
    domains: list[str]


class DomainGrantReq(BaseModel):
    domain: str
    domain_role: Literal["member", "admin"] = "member"


class UserDomainGrantsReq(BaseModel):
    grants: list[DomainGrantReq]


class AddDomainUserReq(BaseModel):
    username: str


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


def _validate_domain(domain: str) -> str:
    cleaned = domain.strip()
    try:
        resolve_domain(cleaned)
    except Exception as exc:
        raise HTTPException(400, f"invalid_domain:{cleaned}") from exc
    return cleaned


def _domain_access_payload(user: dict[str, Any], grants: list[dict[str, str]]) -> dict[str, Any]:
    capabilities_by_domain = {
        grant["domain"]: (
            ["domain.kbs.manage", "domain.users.manage"]
            if grant["domain_role"] == "admin" else []
        )
        for grant in grants
    }
    return {
        "site_role": user["site_role"],
        "grants": grants,
        "capabilities_by_domain": capabilities_by_domain,
    }


def require_domain_admin_credential(
    target: dict[str, Any], grants: list[dict[str, str]],
) -> None:
    """Privileged domain grants require a password-backed account.

    Ordinary members currently use the intranet SSO placeholder, which accepts
    an allow-listed username without a second factor.  Until real SSO lands,
    granting domain-wide administration to such an account would turn knowing
    the username into control of every KB in the domain.
    """
    if any(grant.get("domain_role") == "admin" for grant in grants) \
            and not target.get("password_hash"):
        raise HTTPException(400, "domain_admin_requires_password")


async def _require_domain_manager(
    *, user: dict[str, Any], domain: str, kbdb: KbDB,
) -> str:
    cleaned = _validate_domain(domain)
    if not await kbdb.can_manage_domain(user_id=user["id"], domain=cleaned):
        raise HTTPException(403, "domain_admin_required")
    return cleaned


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
    """验钥 → 绑定身份与开放库（钥匙级，批次2 T5）。miss（无钥/已轮换/已
    吊销）→ 401；钥匙域已解绑 → 403 domain_not_bound（N4 未绑定联动）。

    返回 username 与 open_kb_ids：MCP 免二次查询；开放库 ∩ 实时权限由检索层
    authorize 兜底（开放了但权限被收窄的库在检索时自然 403/剔除）。
    单域钥匙：无 domains 字段——域由 key_domain 定死（N3）。
    """
    kbdb = await get_kb_db(request)
    result = await McpKeyService(kbdb).verify_key(body.key)
    if result is None:
        raise HTTPException(401, "invalid mcp key")

    if not await kbdb.can_create_in_domain(
        user_id=result["user_id"], domain=result["domain"],
    ):
        raise HTTPException(403, detail={
            "code": "domain_not_bound",
            "message": "该钥匙绑定的知识域已被解绑，请联系管理员重新分配后重建钥匙",
        })

    # 读时归一（db 存的是原始值；旧形状迁移持久化由 T9 backfill 承担）
    open_tools = result.get("open_tools")
    normalized = normalize_legacy_open_tools(open_tools or [])
    if normalized is not None:
        open_tools = normalized

    return {
        "ok": True,
        "username": result["username"],
        "user_id": result["user_id"],
        "key_id": result["key_id"],
        "key_domain": result["domain"],
        "open_kb_ids": result["open_kb_ids"],
        # kb_names → id 的解析源：开放库 id+name+domain（软删库自动从清单消失）
        "open_kbs": result.get("open_kbs", []),
        # 批次7：工具开关 / 提示词 / 工具描述（MCP 免二次查）
        "open_tools": open_tools,
        "instructions": result.get("instructions"),
        "tool_descriptions": result.get("tool_descriptions"),
    }
@router.get("/internal/users/{username}/domains", dependencies=[Depends(_require_internal)])
async def internal_user_domains(
    username: str,
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """51号批次1：main_control 域列表过滤用（用户名→绑定域集）。

    不按角色短路——main_control 自己按 JWT role 处理 admin；
    不存在/非 active 用户 → 空列表（不报错，反探测语义同 login）。
    """
    user = await kbdb.get_user_by_username(username)
    if not user or user.get("status") != "active":
        return {"domains": []}
    return {"domains": await kbdb.list_user_domains(user_id=user["id"])}


@router.get("/internal/users/{username}/domain-access", dependencies=[Depends(_require_internal)])
async def internal_user_domain_access(
    username: str,
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """Trusted control-plane projection used to scope domain menus and proxy checks."""
    user = await kbdb.get_user_by_username(username)
    if not user or user.get("status") != "active":
        return {"site_role": None, "grants": [], "capabilities_by_domain": {}}
    grants = await kbdb.list_domain_grants(user_id=user["id"])
    return _domain_access_payload(user, grants)


@router.get("/internal/domains/{domain}/kb-count", dependencies=[Depends(_require_internal)])
async def internal_kb_count(
    domain: str,
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """51号批次1：main_control 删域保护用（域内 active KB 数）。"""
    return {"domain": domain, "kb_count": await kbdb.count_kbs_by_domain(domain=domain)}


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


@router.get("/domain-access/me")
async def my_domain_access(
    user: dict = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    grants = await kbdb.list_domain_grants(user_id=user["id"])
    return _domain_access_payload(user, grants)


# ---------------------------------------------------------------- 域分配 (admin, 51号批次1)

@router.get("/admin/users/{user_id}/domains")
async def get_user_domains(
    user_id: str,
    _admin: dict = Depends(require_admin),
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    user = await kbdb.get_user(user_id)
    if not user:
        raise HTTPException(404, "user_not_found")
    return {"user_id": user_id, "domains": await kbdb.list_user_domains(user_id=user_id)}


@router.post("/admin/users/{user_id}/domains")
async def assign_user_domains(
    user_id: str,
    body: UserDomainsReq,
    _admin: dict = Depends(require_admin),
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    user = await kbdb.get_user(user_id)
    if not user:
        raise HTTPException(404, "user_not_found")
    domains = sorted({d.strip() for d in body.domains if d and d.strip()})
    if not domains:
        raise HTTPException(422, "domains_must_not_be_empty")  # 51号：不允许零绑定
    for d in domains:
        try:
            resolve_domain(d)
        except Exception as exc:
            raise HTTPException(400, f"invalid_domain:{d}") from exc
    if user["site_role"] == "admin":
        return {"user_id": user_id, "domains": []}  # admin 免绑定，幂等空操作
    try:
        updated = await kbdb.set_user_domains(user_id=user_id, domains=domains)
    except DomainMembershipConflict as exc:
        raise HTTPException(409, exc.code) from None
    return {
        "user_id": user_id,
        "domains": updated,
        "domain_grants": await kbdb.list_domain_grants(user_id=user_id),
    }


@router.get("/admin/users/{user_id}/domain-grants")
async def get_user_domain_grants(
    user_id: str,
    _admin: dict = Depends(require_admin),
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    user = await kbdb.get_user(user_id)
    if not user:
        raise HTTPException(404, "user_not_found")
    grants = await kbdb.list_domain_grants(user_id=user_id)
    return {"user_id": user_id, "domains": [g["domain"] for g in grants], "domain_grants": grants}


@router.put("/admin/users/{user_id}/domain-grants")
async def assign_user_domain_grants(
    user_id: str,
    body: UserDomainGrantsReq,
    _admin: dict = Depends(require_admin),
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    user = await kbdb.get_user(user_id)
    if not user:
        raise HTTPException(404, "user_not_found")
    if user["site_role"] == "admin":
        return {"user_id": user_id, "domains": [], "domain_grants": []}
    normalized: dict[str, str] = {}
    for grant in body.grants:
        domain = _validate_domain(grant.domain)
        if domain in normalized:
            raise HTTPException(422, f"duplicate_domain:{domain}")
        normalized[domain] = grant.domain_role
    if not normalized:
        raise HTTPException(422, "domain_grants_must_not_be_empty")
    normalized_grants = [
        {"domain": domain, "domain_role": role}
        for domain, role in normalized.items()
    ]
    require_domain_admin_credential(user, normalized_grants)
    try:
        grants = await kbdb.set_domain_grants(
            user_id=user_id,
            grants=normalized_grants,
        )
    except DomainMembershipConflict as exc:
        raise HTTPException(409, exc.code) from None
    return {"user_id": user_id, "domains": [g["domain"] for g in grants], "domain_grants": grants}


@router.get("/domains/{domain}/users")
async def list_domain_users(
    domain: str,
    user: dict = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    cleaned = await _require_domain_manager(user=user, domain=domain, kbdb=kbdb)
    return {"domain": cleaned, "users": await kbdb.list_domain_users(domain=cleaned)}


@router.post("/domains/{domain}/users", status_code=201)
async def add_domain_user(
    domain: str,
    body: AddDomainUserReq,
    user: dict = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    cleaned = await _require_domain_manager(user=user, domain=domain, kbdb=kbdb)
    target = await kbdb.get_user_by_username(body.username.strip())
    if not target or target.get("status") != "active":
        raise HTTPException(404, "user_not_found")
    if target.get("site_role") == "admin":
        raise HTTPException(400, "site_admin_does_not_require_domain_grant")
    existing_role = await kbdb.get_domain_role(user_id=target["id"], domain=cleaned)
    if existing_role == "admin" and user.get("site_role") != "admin":
        raise HTTPException(403, "cannot_manage_domain_admin")
    await kbdb.bind_domain(user_id=target["id"], domain=cleaned)
    return {
        "id": target["id"],
        "username": target["username"],
        "display_name": target.get("display_name"),
        "domain_role": existing_role or "member",
    }


@router.delete("/domains/{domain}/users/{user_id}")
async def remove_domain_user(
    domain: str,
    user_id: str,
    user: dict = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    cleaned = await _require_domain_manager(user=user, domain=domain, kbdb=kbdb)
    target = await kbdb.get_user(user_id)
    if not target:
        raise HTTPException(404, "user_not_found")
    if target.get("site_role") == "admin" and user.get("site_role") != "admin":
        raise HTTPException(403, "cannot_manage_site_admin")
    role = await kbdb.get_domain_role(user_id=user_id, domain=cleaned)
    if role is None:
        raise HTTPException(404, "domain_membership_not_found")
    if role == "admin" and user.get("site_role") != "admin":
        raise HTTPException(403, "cannot_manage_domain_admin")
    try:
        await kbdb.unbind_domain(user_id=user_id, domain=cleaned)
    except DomainMembershipConflict as exc:
        raise HTTPException(409, exc.code) from None
    return {"ok": True, "user_id": user_id, "domain": cleaned}
