"""阶段 A（批次5）：用户级 MCP 接入服务——一人一钥 + 开放库清单。

密钥形态：kbm_ + 32 字节随机 hex。明文仅生成响应返回一次；库内只存 sha256 hex。
轮换语义：重新生成覆盖 key_hash，旧钥立即失效（无并存期、无宽限期）。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.kb_service import KbError


class McpAccessError(KbError):
    pass


#: 密钥前缀（识别用）；总长 = 4 + 64 = 68 字符。
KEY_PREFIX_TAG = "kbm_"
_KEY_RANDOM_BYTES = 32

#: MCP 工具族三件套（2026-08-31 用户两轮拍板"功能类似必须合并"）——open_tools
#: 白名单与描述键的校验基线，与 mcp_server 的工具注册一一对应：
#: - get_knowledge = get_content + browse_knowledge + inspect_knowledge +
#:   navigate_structure + query_structured_asset（一切读取行为）
MCP_TOOL_NAMES = frozenset({
    "search_knowledge",
    "get_knowledge",
    "upload_document",
})

#: 工具族合并改名映射（2026-08-31 两轮 9→7→3）：旧名 → 新名。任一旧源开启
#: 即新工具开启；全部旧源都不在清单（=显式关闭）则新工具不开启（关闭语义优先）。
_RENAMED_TOOLS = {
    "get_evidence": "get_knowledge",
    "get_document": "get_knowledge",
    "list_knowledge_bases": "get_knowledge",
    "list_documents": "get_knowledge",
    "get_content": "get_knowledge",
    "browse_knowledge": "get_knowledge",
    "inspect_knowledge": "get_knowledge",
    "navigate_structure": "get_knowledge",
    "query_structured_asset": "get_knowledge",
}


def normalize_legacy_open_tools(open_tools: list[str]) -> list[str] | None:
    """跨版本 open_tools 迁移的纯函数（29号 退役迁移 + 2026-08-31 合并改名）。

    规则按序应用：①合并改名（保序去重）②剔除退役名（get_segment_fulltext 等，
    即不在白名单也不在改名映射的名字）。非 legacy 集合（全部在当前白名单内）
    返回 None = 无需迁移。
    """
    if not open_tools or all(t in MCP_TOOL_NAMES for t in open_tools):
        return None
    renamed: list[str] = []
    for t in open_tools:
        new = _RENAMED_TOOLS.get(t, t)
        if new not in renamed:
            renamed.append(new)
    return [t for t in renamed if t in MCP_TOOL_NAMES]


MCP_INSTRUCTIONS_MAX = 4000
MCP_TOOL_DESC_MAX = 2000


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
        """本人视角：密钥状态（无明文/hash）+ 开放库列表。

        open_tools 读时归一（合并改名/退役剔除，纯内存变换不回写——UI 开关
        因此不会因旧工具名显示成"全关"；verify 路径的迁移会持久化终态）。"""
        row = await self._db.get_mcp_access(user_id)
        if row and row.get("open_tools"):
            normalized = normalize_legacy_open_tools(list(row["open_tools"]))
            if normalized is not None:
                row["open_tools"] = normalized
        return row

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
        """全量覆盖开放库：**静默剔除**当前不可见的库（软删/权限收走=合法演化，
        不阻塞保存——批次7 bug：幽灵勾选整单被 not visible 拒）。返回实际生效
        列表，调用方以响应为准刷新界面；不可见 id 不报错不生效，无存在性泄露。"""
        seen: set[str] = set()
        unique: list[str] = []
        for kb_id in kb_ids:
            if kb_id not in seen:
                seen.add(kb_id)
                unique.append(kb_id)
        effective = [
            kb_id for kb_id in unique
            if await self._db.is_visible(kb_id=kb_id, user_id=user_id)
        ]
        dropped = len(unique) - len(effective)
        if dropped:
            logging.getLogger(__name__).info(
                "replace_open_kbs: dropped %d stale/invisible kb(s) for user %s",
                dropped, user_id,
            )
        return await self._db.replace_open_kbs(user_id, effective)

    # ------------------------------------------------ 批次7：工具开关 / 提示词

    async def update_config(
        self,
        *,
        user_id: str,
        open_tools: list[str] | None = None,
        instructions: str | None = None,
        tool_descriptions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """工具开关与文案配置。None = 不改；open_tools 全量白名单（至少一项，
        至少保留一个工具）；instructions 空串=恢复默认；tool_descriptions 全量提交。"""
        if open_tools is not None:
            unknown = [t for t in open_tools if t not in MCP_TOOL_NAMES]
            if unknown:
                raise McpAccessError(f"unknown tool names: {', '.join(unknown)}")
            if not open_tools:
                raise McpAccessError("至少保留一个 MCP 工具")
        if instructions is not None and len(instructions) > MCP_INSTRUCTIONS_MAX:
            raise McpAccessError(
                f"提示词过长（{len(instructions)}/{MCP_INSTRUCTIONS_MAX} 字符）")
        if tool_descriptions is not None:
            bad_keys = [k for k in tool_descriptions if k not in MCP_TOOL_NAMES]
            if bad_keys:
                raise McpAccessError(
                    f"unknown tool names in descriptions: {', '.join(bad_keys)}")
            for name, text in tool_descriptions.items():
                if not isinstance(text, str) or len(text) > MCP_TOOL_DESC_MAX:
                    raise McpAccessError(
                        f"工具 {name} 描述过长（上限 {MCP_TOOL_DESC_MAX} 字符）")
        # '' → None：空提示词即恢复默认文案
        normalized_instructions = (
            instructions.strip() or None if instructions is not None else None
        )
        await self._db.update_mcp_config(
            user_id,
            open_tools=open_tools,
            instructions=normalized_instructions,
            tool_descriptions=tool_descriptions,
        )
        status = await self.get_status(user_id=user_id)
        return status or {"configured": False}
