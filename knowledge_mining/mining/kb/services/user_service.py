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


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LEN:
        raise UserError(f"password too short (<{_MIN_PASSWORD_LEN})")


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
        self, *, user_id: str, display_name: str | None = None,
        site_role: str | None = None, status: str | None = None,
    ) -> dict[str, Any]:
        if site_role is not None and site_role not in _VALID_ROLES:
            raise InvalidRole(site_role)
        updated = await self._db.update_user(
            user_id, display_name=display_name, site_role=site_role, status=status,
        )
        if updated is None:
            raise UserNotFound(user_id)
        return updated

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
        """登录校验：返回 user（含 site_role/display_name）或 None。"""
        user = await self._db.get_user_by_username(username)
        if user is None or not user.get("password_hash"):
            return None
        if user.get("status") == "disabled":
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        return user
