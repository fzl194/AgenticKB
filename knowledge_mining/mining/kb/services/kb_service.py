"""KbService — KB business logic over KbDB.

职责：domain 合法性校验 + 读/写权限 enforcement + 业务错误（NotFound/Forbidden/
Duplicate/InvalidDomain）。Route 层捕获这些错误翻译成 HTTP 状态。

权限模型（需求 §7.2）：
- read = owner OR visibility='public' OR 是 kb_members 成员
- write = owner OR kb_members 中 role='editor'
均要求 KB status='active'（软删的 KB 对所有人视为不存在 → 404）。
"""
from __future__ import annotations

from typing import Any

from psycopg.errors import UniqueViolation

from knowledge_mining.mining.infra.domain_pack import resolve_domain
from knowledge_mining.mining.kb.db import KbDB


# ----------------------------------------------------------------- errors

class KbError(Exception):
    """Base for KB business errors."""


class NotFound(KbError):
    pass


class Forbidden(KbError):
    pass


class Duplicate(KbError):
    pass


class InvalidDomain(KbError):
    pass


class InvalidVisibility(KbError):
    pass


class InvalidName(KbError):
    pass


# ----------------------------------------------------------------- service

class KbService:
    def __init__(self, db: KbDB) -> None:
        self._db = db

    # ----- KB CRUD -----

    async def create_kb(
        self, *, domain: str, name: str, owner_id: str,
        visibility: str = "private", description: str | None = None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        _validate_domain(domain)
        _validate_visibility(visibility)
        name = normalize_kb_name(name)
        try:
            return await self._db.create_kb(
                domain=domain, name=name, owner_id=owner_id,
                visibility=visibility, description=description, metadata=metadata,
            )
        except UniqueViolation as exc:
            raise Duplicate(f"{domain}/{name}") from exc

    async def list_visible(self, *, user_id: str, domain: str) -> list[dict[str, Any]]:
        _validate_domain(domain)
        return await self._db.list_visible(user_id=user_id, domain=domain)

    async def get_kb(self, *, kb_id: str, user_id: str) -> dict[str, Any]:
        kb = await self._db.get_kb(kb_id)
        if kb is None:
            raise NotFound(kb_id)
        await self._assert_read(kb_id, user_id)
        # 批次4：详情附 readiness 四档（纯查询派生，失败不阻塞详情本身）
        try:
            kb["readiness"] = await self._db.get_kb_readiness(kb_id)
        except Exception:  # pragma: no cover - 缺表/权限等环境问题
            kb["readiness"] = None
        return kb

    async def update_kb(
        self, *, kb_id: str, actor_id: str, fields: dict[str, Any],
    ) -> dict[str, Any]:
        await self._assert_write(kb_id, actor_id)
        if "visibility" in fields:
            _validate_visibility(fields["visibility"])
        if "name" in fields:
            fields = {**fields, "name": normalize_kb_name(fields["name"])}
        try:
            updated = await self._db.update_kb(kb_id, fields=fields)
        except UniqueViolation as exc:
            raise Duplicate(f"duplicate active KB name: {fields.get('name')}") from exc
        if updated is None:
            raise NotFound(kb_id)
        return updated

    async def soft_delete(self, *, kb_id: str, actor_id: str) -> dict[str, Any]:
        if not await self._db.is_visible(kb_id=kb_id, user_id=actor_id):
            raise NotFound(kb_id)
        # Deleting the whole container is lifecycle administration, not an
        # ordinary editor write.  Only owner/site-admin may delete or restore.
        if not await self._db.can_restore(kb_id=kb_id, user_id=actor_id):
            raise Forbidden(kb_id)
        deleted = await self._db.soft_delete(kb_id)
        if deleted is None:
            raise NotFound(kb_id)
        return deleted

    async def restore_kb(self, *, kb_id: str, actor_id: str) -> dict[str, Any]:
        kb = await self._db.get_kb(kb_id, include_deleted=True)
        if kb is None or not await self._db.can_restore(
            kb_id=kb_id, user_id=actor_id,
        ):
            # Do not disclose deleted private KB existence to other users.
            raise NotFound(kb_id)
        if kb.get("status") == "active":
            return kb
        try:
            restored = await self._db.restore_kb(kb_id)
        except UniqueViolation as exc:
            raise Duplicate(f"duplicate active KB name: {kb.get('name')}") from exc
        if restored is None:
            # Idempotent race: another authorized request may have restored the
            # row after our initial deleted-state read.
            current = await self._db.get_kb(kb_id, include_deleted=True)
            if current is not None and current.get("status") == "active":
                return current
            raise NotFound(kb_id)
        return restored

    # ----- members -----

    async def add_member(
        self, *, kb_id: str, actor_id: str, username: str, role: str = "viewer",
    ) -> dict[str, Any]:
        await self._assert_write(kb_id, actor_id)
        # 目标用户必须已存在(admin 创建,或已登录过被 current_user 建过行)。
        # 不再 upsert —— 防止 owner 随手输陌生用户名而自动注册垃圾账号、抢占用户名、
        # 绕过 admin 准入控制。
        member = await self._db.get_user_by_username(username)
        if member is None:
            raise NotFound(f"user {username!r} not found")
        # public 库全员可读,viewer 成员冗余(对 public 库只有 editor 成员还有写权限意义)。
        kb = await self._db.get_kb(kb_id)
        if kb and kb.get("visibility") == "public" and role == "viewer":
            raise InvalidVisibility(
                "public 库无需添加只读成员(全员可读);如需协作请加编辑者"
            )
        return await self._db.add_member(kb_id=kb_id, user_id=member["id"], role=role)

    async def list_members(self, *, kb_id: str, user_id: str) -> list[dict[str, Any]]:
        await self._assert_read(kb_id, user_id)
        return await self._db.list_members(kb_id)

    async def remove_member(self, *, kb_id: str, actor_id: str, user_id: str) -> None:
        await self._assert_write(kb_id, actor_id)
        await self._db.remove_member(kb_id=kb_id, user_id=user_id)

    # ----- permission helpers -----

    async def _assert_read(self, kb_id: str, user_id: str) -> None:
        # 看不到（别人的 private / 软删 / 不存在）统一 NotFound——不泄露存在性。
        if not await self._db.is_visible(kb_id=kb_id, user_id=user_id):
            raise NotFound(kb_id)

    async def _assert_write(self, kb_id: str, user_id: str) -> None:
        # 先读权限：看不到 → NotFound（不泄露）；看得到但不能写 → Forbidden。
        if not await self._db.is_visible(kb_id=kb_id, user_id=user_id):
            raise NotFound(kb_id)
        if not await self._db.can_write(kb_id=kb_id, user_id=user_id):
            raise Forbidden(kb_id)


def _validate_domain(domain: str) -> None:
    """domain 必须非空且在 domain_registry.yaml 合法域内。"""
    if not domain or not domain.strip():
        raise InvalidDomain(domain)
    try:
        resolve_domain(domain.strip())
    except Exception as exc:
        raise InvalidDomain(domain) from exc


_VALID_VISIBILITY = {"private", "public"}
_MAX_KB_NAME_LENGTH = 80


def normalize_kb_name(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidName("knowledge base name must be a string")
    normalized = value.strip()
    if not normalized:
        raise InvalidName("knowledge base name must not be blank")
    if len(normalized) > _MAX_KB_NAME_LENGTH:
        raise InvalidName(
            f"knowledge base name must be at most {_MAX_KB_NAME_LENGTH} characters"
        )
    return normalized


def _validate_visibility(visibility: str | None) -> None:
    """visibility 只允许 private / public(shared 已砍)。None 或其它值 → 400。"""
    if visibility not in _VALID_VISIBILITY:
        raise InvalidVisibility(visibility if visibility is not None else "null")
