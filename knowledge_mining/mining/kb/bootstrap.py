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

# 占位符：auth.yaml 样板里的默认值，拒绝用它播种（防误部署留下已知密码）。
_PLACEHOLDER_PASSWORDS = {"", "change-me-on-first-login"}


async def seed_initial_admin(pool: Any, *, admin_password: str) -> None:
    """若无可登录 admin，播种 admin/<admin_password>。幂等。"""
    if admin_password in _PLACEHOLDER_PASSWORDS:
        logger.warning(
            "bootstrap: admin_password 为空或仍是样板占位符，跳过播种 "
            "（请在 auth.yaml 设真实密码后再重启）"
        )
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
    elif existing.get("password_hash"):
        # admin 用户名已存在且已设密码（有人故意配过）—— 不覆盖。
        logger.info("bootstrap: admin 用户名已存在且有密码，跳过（不覆盖）")
        return
    else:
        # admin 用户名存在但无密码（如 Phase 1 upsert 出来的行）→ 提权 + 设密
        await db.update_user(existing["id"], site_role="admin")
        await db.set_password_hash(existing["id"], hashed)
    logger.warning(
        "bootstrap: 已播种首 admin（用户名 admin）—— 请尽快登录改密并从 auth.yaml 移除 bootstrap.admin_password"
    )
