"""身份解析 —— Phase 2：X-KB-User + X-Internal-Auth 双校验。

信任模型：mining:8901 被 publish 到宿主机，单凭 X-KB-User 可被直连伪造身份。
故 current_user 额外要求 X-Internal-Auth == auth.yaml.internal_verify_secret
（只有已鉴权网关能产出该头）。这堵死全部 /api/kb/* 的身份伪造。
site_role 由库现查（require_admin），不靠 X-KB-Role 头。
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from knowledge_mining.mining.infra.control_plane import get_internal_verify_secret
from knowledge_mining.mining.kb.db import KbDB


async def current_user(request: Request) -> dict[str, Any]:
    """Resolve caller from X-KB-User + X-Internal-Auth; upsert kb_users row."""
    username = request.headers.get("X-KB-User", "").strip()
    if not username:
        raise HTTPException(401, "missing X-KB-User header")
    secret = get_internal_verify_secret()
    if not secret:
        # auth.yaml 未就绪（启动期控制面不可达）—— 一律拒，避免无 secret 时放行
        raise HTTPException(401, "auth not initialized")
    if request.headers.get("X-Internal-Auth", "") != secret:
        raise HTTPException(401, "unauthenticated")
    db = KbDB(request.app.state.pg_pool)
    user = await db.upsert_user_by_username(username)
    # disabled 账号即使持有有效 JWT（12h 内）也立即失效 —— 禁用要即时生效。
    if user.get("status") == "disabled":
        raise HTTPException(401, "account disabled")
    return user


async def require_admin(request: Request) -> dict[str, Any]:
    """current_user + site_role 必须为 admin（现查库，纵深防御）。"""
    user = await current_user(request)
    if user.get("site_role") != "admin":
        raise HTTPException(403, "admin required")
    return user
