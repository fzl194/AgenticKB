"""启动期播种首个可登录 admin（破鸡生蛋）。

幂等：仅当 kb_users 中不存在任何 site_role='admin' AND password_hash IS NOT NULL
的行时，才把 admin 用户（不存在则建）提权为 admin 并设密码。
"""
from __future__ import annotations

import logging
from typing import Any

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.security import hash_password

logger = logging.getLogger(__name__)


async def seed_initial_admin(pool: Any, *, admin_password: str) -> None:
    """若无可登录 admin，播种 admin/<admin_password>。幂等。"""
    if not admin_password:
        logger.warning("bootstrap.admin_password 为空，跳过播种首 admin")
        return
    db = KbDB(pool)
    if await db.has_admin():
        logger.info("bootstrap: 已有可登录 admin，跳过播种")
        return
    hashed = hash_password(admin_password)
    existing = await db.get_user_by_username("admin")
    if existing is None:
        await db.create_user(
            username="admin", password_hash=hashed, site_role="admin",
            display_name="Administrator",
        )
    else:
        # admin 用户名存在但不是可登录 admin（如 Phase 1 upsert 出来的无密码行）→ 提权 + 设密
        await db.update_user(existing["id"], site_role="admin")
        await db.set_password_hash(existing["id"], hashed)
    logger.warning(
        "bootstrap: 已播种首 admin（用户名 admin）—— 请尽快登录改密并从 auth.yaml 移除 bootstrap.admin_password"
    )
