"""阶段 A（批次5）：MCP 用户身份——强制密钥验钥与开放库解析。

每次工具调用验一次（不缓存身份）：内网 localhost 调用开销可忽略，换来"吊销/轮换
立即生效、无 60s 绕过窗口"的语义。mining 侧另有 last_used_at 节流，不会写放大。

内部共享密钥（X-Internal-Auth）与 mining 同源：优先 env MCP_INTERNAL_AUTH_SECRET，
否则从 main_control 的 /api/v1/system/auth/raw 拉取（300s 缓存）——同一份 auth.yaml
是唯一真相源，不在 supervisord 里再复制一份。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

MINING_URL = os.environ.get("MINING_URL", "http://localhost:8901").rstrip("/")
VERIFY_TIMEOUT = float(os.environ.get("MCP_VERIFY_TIMEOUT", "10.0"))
CONTROL_PLANE_URL = os.environ.get(
    "CONTROL_PLANE_BASE_URL", "http://localhost:8910"
).rstrip("/")
AUTH_SECRET_TTL = float(os.environ.get("MCP_AUTH_SECRET_TTL", "300.0"))

KEY_PREFIX_TAG = "kbm_"

_secret_cache: dict = {"value": None, "fetched_at": 0.0}


def _internal_auth_secret() -> str:
    """X-Internal-Auth 共享密钥：env 显式配置优先，否则从 main_control 拉取（带缓存）。

    返回空串 = 未就绪（验钥必败 → 401）；拒 change-me 占位符（同 mining 语义）。
    """
    explicit = os.environ.get("MCP_INTERNAL_AUTH_SECRET", "").strip()
    if explicit:
        return explicit
    now = time.monotonic()
    if _secret_cache["value"] and (now - _secret_cache["fetched_at"]) < AUTH_SECRET_TTL:
        return _secret_cache["value"]
    try:
        import yaml

        resp = httpx.get(
            f"{CONTROL_PLANE_URL}/api/v1/system/auth/raw",
            timeout=5.0,
            trust_env=False,
        )
        resp.raise_for_status()
        val = str((yaml.safe_load(resp.text) or {}).get("internal_verify_secret") or "")
        if val and not val.startswith("change-me"):
            _secret_cache["value"] = val
            _secret_cache["fetched_at"] = now
            return val
        logger.warning("auth.raw 无有效 internal_verify_secret")
    except Exception as exc:
        logger.warning("拉取 internal_verify_secret 失败（%s）", exc)
    return ""


class IdentityError(Exception):
    """无钥/错钥/后端不可达。message 面向 Agent（中文），可直接作为工具错误返回。"""


@dataclass(frozen=True)
class Identity:
    username: str
    user_id: str
    #: 开放库 [{id, name}]——kb_names → id 的唯一解析源
    open_kbs: tuple[dict, ...]

    @property
    def open_kb_ids(self) -> list[str]:
        return [k["id"] for k in self.open_kbs]


def extract_bearer_token(headers) -> str | None:
    """从 MCP 请求头取 Bearer 密钥；缺失/非 Bearer → None。"""
    auth = ""
    try:
        auth = headers.get("authorization") or ""
    except Exception:  # pragma: no cover - 非 starlette headers 的兜底
        auth = ""
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def require_identity(headers) -> Identity:
    """强制鉴权入口：无钥/错钥/轮换后旧钥 → IdentityError（401 语义）。"""
    token = extract_bearer_token(headers)
    if token is None:
        raise IdentityError(
            "缺少 MCP 接入密钥：请在 Authorization 头携带 Bearer <密钥>。"
            "密钥在平台「个人设置 → MCP 接入」生成。"
        )
    secret = _internal_auth_secret()
    if not secret:
        logger.error("internal_verify_secret 未就绪，无法验钥")
        raise IdentityError("MCP 服务端未完成鉴权配置，请联系管理员。")

    try:
        resp = httpx.post(
            f"{MINING_URL}/api/kb/auth/mcp-key-verify",
            json={"key": token},
            headers={"X-Internal-Auth": secret},
            timeout=VERIFY_TIMEOUT,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        logger.warning("mcp key verify unreachable: %s", exc)
        raise IdentityError("身份校验服务暂不可用，请稍后重试。") from None

    if resp.status_code == 401:
        raise IdentityError(
            "MCP 接入密钥无效或已被轮换/吊销：请在平台「个人设置 → MCP 接入」"
            "重新生成，并更新 Agent 配置。"
        )
    if resp.status_code != 200:
        logger.warning("mcp key verify returned HTTP %d", resp.status_code)
        raise IdentityError("身份校验失败，请稍后重试。")

    data = resp.json()
    open_kbs = tuple(
        {"id": str(k.get("id") or ""), "name": str(k.get("name") or "")}
        for k in (data.get("open_kbs") or [])
        if isinstance(k, dict) and k.get("id")
    )
    return Identity(
        username=str(data["username"]),
        user_id=str(data.get("user_id") or ""),
        open_kbs=open_kbs,
    )


def resolve_kb_ids(identity: Identity, kb_names: list[str] | None) -> list[str]:
    """kb_names → 开放库 id 列表（16 号方案 §2 ③）。

    - 未传 → 全部开放库
    - 传了 → 必须全部命中开放库（按 name 精确匹配，大小写不敏感）；
      命不中 → IdentityError（报"未开放"，不泄露库是否存在）
    - 开放库为 0 → 无论传不传都报"未开放任何知识库"
    """
    if not identity.open_kbs:
        raise IdentityError(
            "当前 MCP 未开放任何知识库：请在平台「个人设置 → MCP 接入」勾选要开放的知识库。"
        )
    if not kb_names:
        return identity.open_kb_ids
    by_name = {str(k["name"]).strip().casefold(): k["id"] for k in identity.open_kbs}
    resolved: list[str] = []
    missing: list[str] = []
    for name in kb_names:
        kb_id = by_name.get(str(name).strip().casefold())
        if kb_id is None:
            missing.append(str(name))
        elif kb_id not in resolved:
            resolved.append(kb_id)
    if missing:
        raise IdentityError(
            f"以下知识库未对你开放或不存在：{ '、'.join(missing) }。"
            f"当前开放：{ '、'.join(k['name'] for k in identity.open_kbs) }。"
        )
    return resolved
