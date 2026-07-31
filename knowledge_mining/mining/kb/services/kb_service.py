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
        return kb

    async def update_kb(
        self, *, kb_id: str, actor_id: str, fields: dict[str, Any],
    ) -> dict[str, Any]:
        await self._assert_write(kb_id, actor_id)
        updated = await self._db.update_kb(kb_id, fields=fields)
        if updated is None:
            raise NotFound(kb_id)
        return updated

    async def soft_delete(self, *, kb_id: str, actor_id: str) -> dict[str, Any]:
        await self._assert_write(kb_id, actor_id)
        deleted = await self._db.soft_delete(kb_id)
        if deleted is None:
            raise NotFound(kb_id)
        return deleted

    # ----- members -----

    async def add_member(
        self, *, kb_id: str, actor_id: str, username: str, role: str = "viewer",
    ) -> dict[str, Any]:
        await self._assert_write(kb_id, actor_id)
        member = await self._db.upsert_user_by_username(username)
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
