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
        self, *, username: str, password: str, site_role: str = "member",
        display_name: str | None = None,
    ) -> dict[str, Any]:
        if site_role not in _VALID_ROLES:
            raise InvalidRole(site_role)
        if not username or not username.strip():
            raise UserError("username required")
        _validate_password(password)
        try:
            return await self._db.create_user(
                username=username.strip(), password_hash=hash_password(password),
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

    async def verify_credentials(self, *, username: str, password: str) -> dict[str, Any] | None:
        """登录校验：返回 user（含 site_role/display_name）或 None。

        用户不存在/无密码/被禁用时仍跑一次 dummy PBKDF2，消除用户存在性时序侧信道。
        """
        user = await self._db.get_user_by_username(username)
        if user is None or not user.get("password_hash") or user.get("status") == "disabled":
            verify_password(password, _DUMMY_HASH)  # 恒定耗时 dummy
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        return user
