"""51号批次2（Task 3）：单域 MCP 钥匙生命周期服务（多钥匙）。

旧 mcp_access_service（一人一钥）已于 Task 5 退役删除；本模块是唯一存续
实现，共享件（generate_mcp_key / MCP_TOOL_NAMES / _RENAMED_TOOLS /
normalize_legacy_open_tools / 文案长度上限）即权威副本。

密钥形态：kbm_ + 32 字节随机 hex；明文仅 create/rotate 响应返回一次，库内
只存 sha256 hex。轮换语义：覆盖 key_hash，旧钥立即失效（无并存期）。

错误族：McpKeyError(422 校验) / KeyNotFound(404) / KeyLimitExceeded(409) /
KeyNameConflict(409) / KeyRevoked(409) / KeyDomainNotBound(403)。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import unicodedata
import uuid
from typing import Any

from psycopg.errors import UniqueViolation

from knowledge_mining.mining.kb.db import KbDB

# 复用建库同源域校验（51号批次1收敛后的唯一入口）
from knowledge_mining.mining.kb.services.kb_service import InvalidDomain, _validate_domain


class McpKeyError(Exception):
    """钥匙异常族基类。路由层单点捕获本类即可：未映射的子类落 422 兜底，
    不会裸 500（异常族演进安全网）。"""


class KeyNotFound(McpKeyError):
    """钥匙不存在或不属于本人（不泄露他人钥匙存在性）。"""


class KeyLimitExceeded(McpKeyError):
    """活跃钥匙数达到上限。"""


class KeyNameConflict(McpKeyError):
    """同域同名活跃钥匙已存在。"""


class KeyRevoked(McpKeyError):
    """对已吊销钥匙执行轮换/配置/开放库操作。"""


class KeyDomainNotBound(McpKeyError):
    """用户未绑定该域（无建钥资格）。"""


#: 活跃钥匙上限（建钥 409 防线）。前端有副本须同步改：kb-ui/src/views/McpAccessView.vue 的 MAX_KEYS。
MAX_KEYS_PER_USER = 10
MAX_KEY_NAME_LEN = 64

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


class McpKeyService:
    def __init__(self, db: KbDB) -> None:
        self._db = db

    # ------------------------------------------------------------ 查询

    async def list_keys(
        self, *, user_id: str, is_admin: bool,
    ) -> list[dict[str, Any]]:
        """本人全部钥匙 + domain_bound 派生（admin 或绑定该域）。

        钥匙 ≤10 把，逐把一次 can_create_in_domain 可接受。open_tools 读时
        归一（纯内存，沿用旧 get_status 语义——UI 不因旧工具名显示成"全关"）。
        """
        rows = await self._db.list_mcp_keys(user_id=user_id)
        result: list[dict[str, Any]] = []
        for row in rows:
            key = dict(row)
            key["domain_bound"] = is_admin or await self._db.can_create_in_domain(
                user_id=user_id, domain=key["domain"],
            )
            if key.get("open_tools"):
                normalized = normalize_legacy_open_tools(list(key["open_tools"]))
                if normalized is not None:
                    key["open_tools"] = normalized
            result.append(key)
        return result

    async def get_key_status(self, *, user_id: str, key_id: str) -> dict[str, Any]:
        """本人视角单钥匙组装：钥匙行 + 当前生效开放库 + 归一 open_tools。"""
        key = await self._require_owned(user_id=user_id, key_id=key_id)
        status = dict(key)
        if status.get("open_tools"):
            normalized = normalize_legacy_open_tools(list(status["open_tools"]))
            if normalized is not None:
                status["open_tools"] = normalized
        status["open_kb_ids"] = await self._db.key_open_kb_ids(key_id=key_id)
        # 终审整改：单钥匙响应自带 domain_bound（与 list_keys 同源语义）——
        # update_config 等返回本组装的端点不再让前端把健康钥匙误标「域已解绑」。
        status["domain_bound"] = await self._db.can_create_in_domain(
            user_id=user_id, domain=status["domain"],
        )
        return status

    # ------------------------------------------------------------ 生命周期

    async def create_key(
        self, *, user_id: str, name: str, domain: str, is_admin: bool,
    ) -> dict[str, Any]:
        """新建单域钥匙。返回明文（仅此一次）。校验序即错误优先级。"""
        cleaned = (name or "").strip()
        if not cleaned:
            raise McpKeyError("钥匙名称不能为空")
        if len(cleaned) > MAX_KEY_NAME_LEN:
            raise McpKeyError(
                f"钥匙名称过长（{len(cleaned)}/{MAX_KEY_NAME_LEN} 字符）")
        if any(unicodedata.category(c) in ("Cc", "Cf") for c in cleaned):
            # Cc=控制字符；Cf=零宽/方向格式符（U+200B 零宽空格、U+202E RTL
            # override 等——视觉欺骗载体，一并拒绝）
            raise McpKeyError("钥匙名称不能包含控制字符")
        try:
            _validate_domain(domain)
        except InvalidDomain as exc:
            raise McpKeyError(f"未知的知识域：{domain}") from exc
        if not is_admin and not await self._db.can_create_in_domain(
            user_id=user_id, domain=domain,
        ):
            raise KeyDomainNotBound(
                f"你未绑定知识域「{domain}」，无法在该域创建 MCP 钥匙")
        if await self._db.count_active_mcp_keys(user_id=user_id) >= MAX_KEYS_PER_USER:
            raise KeyLimitExceeded(
                f"活跃 MCP 钥匙已达上限（{MAX_KEYS_PER_USER} 把），"
                "请先吊销不需要的钥匙")
        plaintext, key_hash, key_prefix = generate_mcp_key()
        key_id = uuid.uuid4().hex
        try:
            row = await self._db.create_mcp_key(
                user_id=user_id, name=cleaned, domain=domain,
                key_hash=key_hash, key_prefix=key_prefix, key_id=key_id,
            )
        except UniqueViolation as exc:
            raise KeyNameConflict(
                f"创建失败：该知识域（{domain}）下你名下已有同名的活跃钥匙"
                f"「{cleaned}」（吊销后可重建同名）") from exc
        return {**row, "key": plaintext}  # 明文仅此一次

    async def rotate_key(self, *, user_id: str, key_id: str) -> dict[str, Any]:
        """轮换：覆盖 key_hash，旧钥立即失效。返回新明文（仅此一次）。"""
        await self._require_owned_active(user_id=user_id, key_id=key_id)
        plaintext, key_hash, key_prefix = generate_mcp_key()
        row = await self._db.rotate_mcp_key(
            key_id=key_id, key_hash=key_hash, key_prefix=key_prefix,
        )
        if row is None:
            # 理论不可达——已校验 active；防御性兜底（并发吊销）
            raise KeyRevoked(f"钥匙已吊销，无法轮换")
        return {
            "key": plaintext,          # 明文，仅此一次返回
            "key_prefix": row["key_prefix"],
            "rotated_at": row["rotated_at"],
        }

    async def revoke_key(self, *, user_id: str, key_id: str) -> None:
        """吊销（幂等：已 revoked 不报错）。"""
        await self._require_owned(user_id=user_id, key_id=key_id)
        await self._db.revoke_mcp_key(key_id=key_id)

    # ------------------------------------------------------------ 开放库 / 配置

    async def replace_open_kbs(
        self, *, user_id: str, key_id: str, kb_ids: list[str],
    ) -> list[str]:
        """全量覆盖钥匙开放库：**静默剔除**当前不可见的库（软删/权限收走=合法
        演化，不阻塞保存）；SQL 层再滤越域/非 active。返回实际生效列表。"""
        await self._require_owned_active(user_id=user_id, key_id=key_id)
        unique = list(dict.fromkeys(kb_ids))
        effective = [
            kb_id for kb_id in unique
            if await self._db.is_visible(kb_id=kb_id, user_id=user_id)
        ]
        dropped = len(unique) - len(effective)
        if dropped:
            logging.getLogger(__name__).info(
                "replace_open_kbs: dropped %d stale/invisible kb(s) "
                "for key %s (user %s)", dropped, key_id, user_id,
            )
        return await self._db.replace_mcp_key_open_kbs(
            key_id=key_id, kb_ids=effective,
        )

    async def update_config(
        self,
        *,
        user_id: str,
        key_id: str,
        open_tools: list[str] | None,
        instructions: str | None,
        tool_descriptions: dict[str, str] | None,
    ) -> dict[str, Any]:
        """钥匙级工具开关与文案配置（校验语义复刻旧 update_config）。

        None = 不改；open_tools 全量白名单（至少一项）；instructions 空串=
        恢复默认；tool_descriptions 全量提交。
        """
        if open_tools is not None:
            unknown = [t for t in open_tools if t not in MCP_TOOL_NAMES]
            if unknown:
                raise McpKeyError(f"unknown tool names: {', '.join(unknown)}")
            if not open_tools:
                raise McpKeyError("至少保留一个 MCP 工具")
        if instructions is not None and len(instructions) > MCP_INSTRUCTIONS_MAX:
            raise McpKeyError(
                f"提示词过长（{len(instructions)}/{MCP_INSTRUCTIONS_MAX} 字符）")
        if tool_descriptions is not None:
            bad_keys = [k for k in tool_descriptions if k not in MCP_TOOL_NAMES]
            if bad_keys:
                raise McpKeyError(
                    f"unknown tool names in descriptions: {', '.join(bad_keys)}")
            for name, text in tool_descriptions.items():
                if not isinstance(text, str) or len(text) > MCP_TOOL_DESC_MAX:
                    raise McpKeyError(
                        f"工具 {name} 描述过长（上限 {MCP_TOOL_DESC_MAX} 字符）")
        await self._require_owned_active(user_id=user_id, key_id=key_id)
        # '' → None：空提示词即恢复默认文案
        normalized_instructions = (
            instructions.strip() or None if instructions is not None else None
        )
        await self._db.update_mcp_key_config(
            key_id=key_id,
            open_tools=open_tools,
            instructions=normalized_instructions,
            tool_descriptions=tool_descriptions,
        )
        return await self.get_key_status(user_id=user_id, key_id=key_id)

    # ------------------------------------------------------------ 验钥

    async def verify_key(self, plaintext: str) -> dict[str, Any] | None:
        """按明文验钥（复刻旧 verify_key）；miss / 吊销 → None（不区分）。"""
        if not plaintext.startswith(KEY_PREFIX_TAG):
            return None
        return await self._db.find_mcp_key_by_hash(hash_mcp_key(plaintext))

    # ------------------------------------------------------------ 内部

    async def _require_owned(self, *, user_id: str, key_id: str) -> dict[str, Any]:
        key = await self._db.get_mcp_key(key_id=key_id)
        if key is None or key["user_id"] != user_id:
            raise KeyNotFound(key_id)
        return key

    async def _require_owned_active(
        self, *, user_id: str, key_id: str,
    ) -> dict[str, Any]:
        key = await self._require_owned(user_id=user_id, key_id=key_id)
        if key["status"] == "revoked":
            raise KeyRevoked(f"钥匙已吊销（key={key_id}）")
        return key
