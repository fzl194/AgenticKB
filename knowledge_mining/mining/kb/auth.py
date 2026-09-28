"""身份解析 —— Phase 2：X-KB-User + X-Internal-Auth 双校验。

信任模型：mining:8901 被 publish 到宿主机，单凭 X-KB-User 可被直连伪造身份。
故 current_user 额外要求 X-Internal-Auth == auth.yaml.internal_verify_secret
（只有已鉴权网关能产出该头）。这堵死全部 /api/kb/* 的身份伪造。
site_role 由库现查（require_admin），不靠 X-KB-Role 头。
"""
from __future__ import annotations

import asyncio
from hmac import compare_digest
from typing import Any

from fastapi import HTTPException, Request

from knowledge_mining.mining.infra.control_plane import get_internal_verify_secret
from knowledge_mining.mining.kb.db import KbDB


class IdentityCache:
    """Bounded identity snapshot cache with database-authoritative account status.

    The old path cached ``status`` for 60 seconds and only invalidated the local
    worker, so another worker could continue accepting a disabled account. A
    cheap SELECT now refreshes authorization state on every request; INSERT is
    still reserved for the exceptional lazy-provisioning miss.
    """

    def __init__(self, *, ttl_seconds: float = 60.0, max_entries: int = 500) -> None:
        self.ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[str, tuple[float, dict]] = {}
        self._lock = asyncio.Lock()

    async def get_user(self, db: Any, username: str) -> dict[str, Any]:
        user = await db.get_user_by_username(username)
        if user is None:
            user = await db.upsert_user_by_username(username)
        async with self._lock:
            self._entries[username] = (0.0, dict(user))
            while len(self._entries) > self._max_entries:
                self._entries.pop(next(iter(self._entries)))
        return user

    def invalidate(self, username: str) -> None:
        self._entries.pop(username, None)

    def size(self) -> int:
        return len(self._entries)


async def authenticate_request(request: Request) -> dict[str, Any]:
    """Resolve and validate the trusted gateway identity once per request."""
    username = request.headers.get("X-KB-User", "").strip()
    if not username:
        raise HTTPException(401, "missing X-KB-User header")
    secret = get_internal_verify_secret()
    if not secret:
        # auth.yaml 未就绪（启动期控制面不可达）—— 一律拒，避免无 secret 时放行
        raise HTTPException(401, "auth not initialized")
    if not compare_digest(request.headers.get("X-Internal-Auth", ""), secret):
        raise HTTPException(401, "unauthenticated")
    db = KbDB(request.app.state.pg_pool)
    cache = getattr(request.app.state, "identity_cache", None)
    if cache is None:
        cache = request.app.state.identity_cache = IdentityCache()
    user = await cache.get_user(db, username)
    # disabled 账号即使持有有效 JWT（12h 内）也立即失效 —— 禁用要即时生效
    # （禁用端点同进程主动 invalidate；TTL 兜底外部改库场景）。
    if user.get("status") == "disabled":
        raise HTTPException(401, "account disabled")
    return user


async def current_user(request: Request) -> dict[str, Any]:
    """Return the API guard's identity or authenticate a standalone route call."""
    user = getattr(request.state, "authenticated_user", None)
    if isinstance(user, dict):
        return user
    user = await authenticate_request(request)
    request.state.authenticated_user = user
    return user


async def require_admin(request: Request) -> dict[str, Any]:
    """current_user + site_role 必须为 admin（现查库，纵深防御）。"""
    user = await current_user(request)
    if user.get("site_role") != "admin":
        raise HTTPException(403, "admin required")
    return user
