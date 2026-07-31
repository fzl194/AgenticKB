"""Phase 1 auth — X-KB-User header injection.

内网白名单前提下可信（设计 §6.2 + §8 安全取舍）。任何能伪造头部者可 upsert
任意 kb_users 行——Phase 2 必须替换为真实登录（SSO/本地账号），届时只换身份来源，
表与权限逻辑零改。
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from knowledge_mining.mining.kb.db import KbDB


async def current_user(request: Request) -> dict[str, Any]:
    """Resolve the caller from X-KB-User header; upsert kb_users row."""
    username = request.headers.get("X-KB-User", "").strip()
    if not username:
        raise HTTPException(401, "missing X-KB-User header")
    db = KbDB(request.app.state.pg_pool)
    return await db.upsert_user_by_username(username)
