"""用户管理业务逻辑（admin 操作 + 改自己密码 + 登录凭证校验）。"""
from __future__ import annotations

from typing import Any

from psycopg.errors import UniqueViolation

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.security import hash_password, verify_password


class UserError(Exception):
    """用户管理业务错误基类。"""


class DuplicateUser(UserError):
    pass


class InvalidRole(UserError):
    pass


class UserNotFound(UserError):
    pass


class WrongPassword(UserError):
    pass


_VALID_ROLES = {"admin", "member"}
_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 1024  # 防 DoS：PBKDF2 对超长密码耗时无上限

# 用户不存在/被禁用时仍做一次 dummy 校验，消除「用户存在与否」的时序侧信道。
_DUMMY_HASH = hash_password("kb-dummy-password-for-constant-timing")


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LEN:
        raise UserError(f"password too short (<{_MIN_PASSWORD_LEN})")
    if len(password) > _MAX_PASSWORD_LEN:
        raise UserError(f"password too long (>{_MAX_PASSWORD_LEN})")


class UserService:
    def __init__(self, db: KbDB) -> None:
        self._db = db

    async def list_users(self) -> list[dict[str, Any]]:
        return await self._db.list_users()

    async def create_user(
        self, *, username: str, password: str | None = None, site_role: str = "member",
        display_name: str | None = None,
    ) -> dict[str, Any]:
        if site_role not in _VALID_ROLES:
            raise InvalidRole(site_role)
        if not username or not username.strip():
            raise UserError("username required")
        if site_role == "admin":
            # admin 必须有密码
            if not password:
                raise UserError("admin 必须设置密码")
            _validate_password(password)
            pw_hash: str | None = hash_password(password)
        else:
            # member（工号）—— 无密码（白名单 = 表里有此行即信任）
            pw_hash = None
        try:
            return await self._db.create_user(
                username=username.strip(), password_hash=pw_hash,
                site_role=site_role, display_name=display_name,
            )
        except UniqueViolation as exc:
            raise DuplicateUser(username) from exc

    async def update_user(
        self, *, user_id: str, actor_id: str | None = None,
        display_name: str | None = None, site_role: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if site_role is not None and site_role not in _VALID_ROLES:
            raise InvalidRole(site_role)
        target = await self._db.get_user(user_id)
        if target is None:
            raise UserNotFound(user_id)
        promoting = site_role == "admin" and target["site_role"] != "admin"
        # 升 admin：目标必须有密码（工号无密码不能直接升 admin）
        if promoting and not target.get("password_hash"):
            raise UserError("升管理员前请先为该用户设置密码")
        demoting = (
            site_role is not None and target["site_role"] == "admin" and site_role != "admin"
        )
        disabling_admin = status == "disabled" and target["site_role"] == "admin"
        # 自我保护：不能禁用或降级自己（否则把自己锁死）。
        if actor_id is not None and actor_id == user_id and (demoting or status == "disabled"):
            raise UserError("不能禁用或降级自己的账号")
        # last-admin 守卫：不能让启用 admin 归零。
        if (demoting or disabling_admin) and await self._db.count_active_admins() <= 1:
            raise UserError("至少保留一个启用的管理员")
        updated = await self._db.update_user(
            user_id, display_name=display_name, site_role=site_role, status=status,
        )
        return updated  # type: ignore[return-value]

    async def reset_password(self, user_id: str, new_password: str) -> None:
        _validate_password(new_password)
        if await self._db.get_user(user_id) is None:
            raise UserNotFound(user_id)
        await self._db.set_password_hash(user_id, hash_password(new_password))

    async def change_own_password(self, *, user_id: str, old: str, new: str) -> None:
        _validate_password(new)
        user = await self._db.get_user(user_id)
        if user is None or not user.get("password_hash"):
            raise UserNotFound(user_id)
        if not verify_password(old, user["password_hash"]):
            raise WrongPassword("old password mismatch")
        await self._db.set_password_hash(user_id, hash_password(new))

    async def verify_intranet_auth(self, username: str) -> bool:
        """【SSO 口子】当前内网鉴权未接入 → 恒 True（白名单 = kb_users 表里有此行即信任）。

        未来：把这里换成「跳内网 SSO → 回跳带 ticket → 校验 ticket」。
        verify_credentials 的 member 分支只调本函数，换 SSO 不动调用方。
        """
        return True

    async def identify(self, username: str) -> dict[str, Any]:
        """登录第一步：按用户名判定登录模式。

        - not_found：不在库 / 被禁用。
        - password：admin 或有密码账号 → 前端弹密码框。
        - member：工号账号（在库、无密码）→ 前端直接登录（未来跳 SSO）。
        """
        user = await self._db.get_user_by_username(username)
        if user is None or user.get("status") == "disabled":
            return {"mode": "not_found"}
        if user.get("password_hash"):
            return {"mode": "password"}
        return {"mode": "member", "display_name": user.get("display_name")}

    async def verify_credentials(
        self, *, username: str, password: str | None = None,
    ) -> dict[str, Any] | None:
        """登录校验：返回 user（含 site_role/display_name）或 None。

        - 有 password_hash（admin）→ 验密码。
        - 无 password_hash（工号 member）→ 走 SSO 口子（verify_intranet_auth）。
        - 不存在 / 禁用 → dummy PBKDF2（恒定耗时）+ None。
        """
        user = await self._db.get_user_by_username(username)
        if user is None or user.get("status") == "disabled":
            verify_password(password or "", _DUMMY_HASH)  # 恒定耗时 dummy
            return None
        if user.get("password_hash"):
            if not verify_password(password or "", user["password_hash"]):
                return None
            return user
        # 工号 member（无密码）→ SSO 口子
        if not await self.verify_intranet_auth(username):
            return None
        return user
