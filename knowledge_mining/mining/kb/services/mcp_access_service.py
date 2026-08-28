"""阶段 A（批次5）：用户级 MCP 接入服务——一人一钥 + 开放库清单。

密钥形态：kbm_ + 32 字节随机 hex。明文仅生成响应返回一次；库内只存 sha256 hex。
轮换语义：重新生成覆盖 key_hash，旧钥立即失效（无并存期、无宽限期）。
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.kb_service import KbError


class McpAccessError(KbError):
    pass


#: 密钥前缀（识别用）；总长 = 4 + 64 = 68 字符。
KEY_PREFIX_TAG = "kbm_"
_KEY_RANDOM_BYTES = 32


def generate_mcp_key() -> tuple[str, str, str]:
    """生成 (明文, key_hash, key_prefix)。明文只此一次。"""
    plaintext = f"{KEY_PREFIX_TAG}{secrets.token_hex(_KEY_RANDOM_BYTES)}"
    return plaintext, hash_mcp_key(plaintext), plaintext[:8]


def hash_mcp_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class McpAccessService:
    def __init__(self, db: KbDB) -> None:
        self._db = db

    async def get_status(self, *, user_id: str) -> dict[str, Any] | None:
        """本人视角：密钥状态（无明文/hash）+ 开放库列表。"""
        return await self._db.get_mcp_access(user_id)

    async def rotate_key(self, *, user_id: str) -> dict[str, Any]:
        """生成或轮换密钥。返回明文（仅此一次）+ 状态。"""
        plaintext, key_hash, key_prefix = generate_mcp_key()
        row = await self._db.upsert_mcp_key(
            user_id, key_hash=key_hash, key_prefix=key_prefix,
        )
        return {
            "key": plaintext,          # 明文，仅此一次返回
            "key_prefix": row["key_prefix"],
            "rotated_at": row["rotated_at"],
        }

    async def verify_key(self, plaintext: str) -> dict[str, Any] | None:
        """按明文验钥 → {username, user_id, open_kb_ids}；miss → None。"""
        if not plaintext.startswith(KEY_PREFIX_TAG):
            return None
        return await self._db.verify_mcp_key(hash_mcp_key(plaintext))

    async def replace_open_kbs(
        self, *, user_id: str, kb_ids: list[str],
    ) -> list[str]:
        """全量覆盖开放库。勾选的每个库必须是本人当前可见库（否则整单拒绝，
        不接受部分生效——避免勾选结果与用户预期悄悄漂移）。"""
        seen: set[str] = set()
        unique: list[str] = []
        for kb_id in kb_ids:
            if kb_id not in seen:
                seen.add(kb_id)
                unique.append(kb_id)
        for kb_id in unique:
            if not await self._db.is_visible(kb_id=kb_id, user_id=user_id):
                raise McpAccessError(f"knowledge base not visible: {kb_id}")
        return await self._db.replace_open_kbs(user_id, unique)
