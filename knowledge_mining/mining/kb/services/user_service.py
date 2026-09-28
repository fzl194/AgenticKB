"""用户管理业务逻辑（admin 操作 + 改自己密码 + 登录凭证校验）。"""
from __future__ import annotations

from typing import Any

from psycopg.errors import UniqueViolation

from knowledge_mining.mining.infra.domain_pack import get_default_domain
from knowledge_mining.mining.kb.db import KbDB, UserDeletionConflict
from knowledge_mining.mining.kb.security import hash_password, verify_password

class UserError(Exception):
    """用户管理业务错误基类。"""


class DuplicateUser(UserError):
    pass


class UsernameReserved(DuplicateUser):
    """A soft-deleted identity retains its login name for safe restoration."""


class InvalidRole(UserError):
    pass


class UserNotFound(UserError):
    pass


class WrongPassword(UserError):
    pass


class UserOwnsKnowledgeBases(UserError):
    def __init__(self, knowledge_bases: list[dict[str, Any]]) -> None:
        self.knowledge_bases = knowledge_bases
        super().__init__("user_owns_knowledge_bases")


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

    async def list_users(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        return await self._db.list_users(include_deleted=include_deleted)

    async def create_user(
        self, *, username: str, password: str | None = None, site_role: str = "member",
        display_name: str | None = None,
    ) -> dict[str, Any]:
        if site_role not in _VALID_ROLES:
            raise InvalidRole(site_role)
        if not username or not username.strip():
            raise UserError("username required")
        cleaned_username = username.strip()
        existing = await self._db.get_user_by_username(cleaned_username)
        if existing is not None:
            if existing.get("deleted_at") is not None:
                raise UsernameReserved("username_reserved")
            raise DuplicateUser(cleaned_username)
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
            if site_role == "member":
                user = await self._db.create_member_with_domain(
                    username=cleaned_username,
                    display_name=display_name,
                    domain=get_default_domain(),
                )
            else:
                user = await self._db.create_user(
                    username=cleaned_username, password_hash=pw_hash,
                    site_role=site_role, display_name=display_name,
                )
        except UniqueViolation as exc:
            raise DuplicateUser(username) from exc
        return user

    async def update_user(
        self, *, user_id: str, actor_id: str | None = None,
        display_name: str | None = None, site_role: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if site_role is not None and site_role not in _VALID_ROLES:
            raise InvalidRole(site_role)
        target = await self._db.get_user(user_id)
        if target is None or target.get("deleted_at") is not None:
            raise UserNotFound(user_id)
        promoting = site_role == "admin" and target["site_role"] != "admin"
        # 升 admin：目标必须有密码（工号无密码不能直接升 admin）
        if promoting and not target.get("password_hash"):
            raise UserError("升管理员前请先为该用户设置密码")
        demoting = (
            site_role is not None and target["site_role"] == "admin" and site_role != "admin"
        )
        disabling_admin = (
            status == "disabled"
            and target["site_role"] == "admin"
            and target.get("status") == "active"
        )
        # 自我保护：不能禁用或降级自己（否则把自己锁死）。
        if actor_id is not None and actor_id == user_id and (demoting or status == "disabled"):
            raise UserError("不能禁用或降级自己的账号")
        # last-admin 守卫：不能让启用 admin 归零。
        removes_active_admin = (
            disabling_admin
            or (demoting and target.get("status") == "active")
        )
        if removes_active_admin and await self._db.count_active_admins() <= 1:
            raise UserError("至少保留一个启用的管理员")
        effective_role = site_role or target["site_role"]
        if status == "active" and effective_role == "member":
            grants = await self._db.list_domain_grants(user_id=user_id)
            if not grants:
                raise UserError("domain_grants_must_not_be_empty")
        if demoting:
            try:
                updated = await self._db.demote_admin_to_member(
                    user_id=user_id, fallback_domain=get_default_domain(),
                )
            except UserDeletionConflict as exc:
                if exc.code == "last_active_admin":
                    raise UserError("至少保留一个启用的管理员") from None
                raise UserNotFound(user_id) from None
            if display_name is None and status is None:
                return updated
            site_role = None
        try:
            updated = await self._db.update_user(
                user_id, display_name=display_name, site_role=site_role, status=status,
            )
        except UserDeletionConflict as exc:
            if exc.code == "last_active_admin":
                raise UserError("至少保留一个启用的管理员") from None
            raise UserNotFound(user_id) from None
        return updated  # type: ignore[return-value]

    async def delete_user(
        self, *, user_id: str, actor_id: str, confirm_username: str,
    ) -> dict[str, Any]:
        target = await self._db.get_user(user_id)
        if target is None or target.get("deleted_at") is not None:
            raise UserNotFound(user_id)
        if actor_id == user_id:
            raise UserError("不能删除自己的账号")
        if confirm_username != target["username"]:
            raise UserError("confirm_username_mismatch")
        if target["site_role"] == "admin" and await self._db.count_active_admins() <= 1:
            raise UserError("至少保留一个启用的管理员")
        owned = await self._db.list_owned_kbs_for_user(user_id=user_id)
        if owned:
            raise UserOwnsKnowledgeBases(owned)
        try:
            return await self._db.delete_user_account(
                user_id=user_id, deleted_by_user_id=actor_id,
            )
        except UserDeletionConflict as exc:
            if exc.code == "user_owns_knowledge_bases":
                raise UserOwnsKnowledgeBases(exc.knowledge_bases) from None
            if exc.code == "last_active_admin":
                raise UserError("至少保留一个启用的管理员") from None
            raise UserNotFound(user_id) from None

    async def deletion_preview(self, *, user_id: str) -> dict[str, Any]:
        target = await self._db.get_user(user_id)
        if target is None or target.get("deleted_at") is not None:
            raise UserNotFound(user_id)
        owned = await self._db.list_owned_kbs_for_user(user_id=user_id)
        public_user = {
            key: target.get(key)
            for key in (
                "id", "username", "display_name", "status", "site_role", "deleted_at",
            )
        }
        counts = await self._db.get_user_deletion_counts(user_id=user_id)
        return {"user": public_user, "owned_knowledge_bases": owned, **counts}

    async def restore_user(self, *, user_id: str) -> dict[str, Any]:
        target = await self._db.get_user(user_id)
        if target is None or target.get("deleted_at") is None:
            raise UserNotFound(user_id)
        try:
            return await self._db.restore_user_account(user_id=user_id)
        except UserDeletionConflict:
            raise UserNotFound(user_id) from None

    async def assign_domain_grants(
        self,
        *,
        user_id: str,
        grants: list[dict[str, str]],
        initial_password: str | None = None,
    ) -> list[dict[str, str]]:
        target = await self._db.get_user(user_id)
        if target is None or target.get("deleted_at") is not None:
            raise UserNotFound(user_id)
        if target["site_role"] == "admin":
            return []
        requires_password = any(grant.get("domain_role") == "admin" for grant in grants)
        password_hash: str | None = None
        if requires_password and not target.get("password_hash"):
            if not initial_password:
                raise UserError("domain_admin_requires_password")
            _validate_password(initial_password)
            password_hash = hash_password(initial_password)
        elif initial_password:
            raise UserError("initial_password_not_required")
        try:
            return await self._db.set_domain_grants_with_password(
                user_id=user_id,
                grants=grants,
                password_hash=password_hash,
            )
        except UserDeletionConflict as exc:
            if exc.code in {
                "domain_admin_requires_password", "password_changed_concurrently",
            }:
                raise UserError(exc.code) from None
            raise UserNotFound(user_id) from None

    async def reset_password(self, user_id: str, new_password: str) -> None:
        _validate_password(new_password)
        target = await self._db.get_user(user_id)
        if target is None or target.get("deleted_at") is not None:
            raise UserNotFound(user_id)
        await self._db.set_password_hash(user_id, hash_password(new_password))

    async def change_own_password(self, *, user_id: str, old: str, new: str) -> None:
        _validate_password(new)
        user = await self._db.get_user(user_id)
        if user is None or user.get("deleted_at") is not None:
            raise UserNotFound(user_id)
        if not user.get("password_hash"):
            # 工号 member 无密码，不能「改密码」→ 400（不是 404）
            raise UserError("账号未设置密码")
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
        if user is None or user.get("status") == "disabled" or user.get("deleted_at") is not None:
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
        if user is None or user.get("status") == "disabled" or user.get("deleted_at") is not None:
            verify_password(password or "", _DUMMY_HASH)  # 恒定耗时 dummy
            return None
        if user.get("password_hash"):
            if not verify_password(password or "", user["password_hash"]):
                return None
            return user
        # 工号 member（无密码）→ 恒定耗时（与上面 admin 路径对齐，防时序侧信道区分 member/admin）
        verify_password(password or "", _DUMMY_HASH)
        # SSO 口子
        if not await self.verify_intranet_auth(username):
            return None
        return user
