"""JWT 鉴权中间件 —— 校验 Authorization Bearer，强制 admin-only 路径白名单。

身份挂 request.state.user；internal_verify_secret 挂 app.state.internal_verify_secret
（供 proxy._build_forward_headers 注入给 mining）。

默认 fail-closed：auth.yaml 缺失或无 enabled 键 → 中间件不启用（passthrough）。
生产 auth.yaml 显式 enabled: true。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from main_control_service.jwt_util import decode as jwt_decode

logger = logging.getLogger(__name__)

# 登录与健康检查不需要 token
_SKIP_PATHS: frozenset[str] = frozenset({
    "/health",
    "/api/v1/auth/login",
    "/api/v1/auth/identify",  # 登录前探测（按用户名判定模式），无 token
})


def _is_admin_only(method: str, path: str) -> bool:
    """admin-only 写路径（member 命中 → 403）。spec §8.1。"""
    if path.startswith("/api/v1/admin/"):
        return True
    if method == "PUT" and path.startswith("/api/v1/system/") and path.endswith("/raw"):
        return True
    if method in {"POST", "PUT", "DELETE"} and path.startswith("/api/v1/domains"):
        return True
    if method in {"GET", "PUT"} and "/scenario/raw" in path and path.startswith("/api/v1/domains/"):
        return True
    if method == "POST" and path == "/api/v1/code-sync":
        return True
    if method == "GET" and path.startswith("/api/v1/logs"):
        return True
    return False


_PLACEHOLDER_PREFIX = "change-me"


def _secret_valid(v: Any) -> bool:
    return isinstance(v, str) and bool(v) and not v.startswith(_PLACEHOLDER_PREFIX)


def _is_open_config_read(method: str, path: str) -> bool:
    """配置中心「读」接口：mining/serving/llm 启动时拉自己的配置（无用户 token），
    品牌域信息等也启动期读。维持开放（与加鉴权前一致）——鉴权只管后端访问/改配置/管理。

    注意：含 /raw 的 YAML 读会暴露 llm_api_key 等；依赖网络层（IP 白名单/防火墙）做边界，
    与本特性之前的状态相同（本特性新增的是 proxy/写/管理的鉴权，net 安全性提升）。
    """
    if method != "GET":
        return False
    if path == "/api/v1/system" or path.startswith("/api/v1/system/"):
        return True
    if path == "/api/v1/serving-config":
        return True
    if path.startswith("/api/v1/domains"):
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, config_path: Path) -> None:
        super().__init__(app)
        self._config_path = config_path
        self._state: dict[str, Any] = {}
        self.reload()

    def reload(self) -> dict[str, object]:
        if self._config_path.exists():
            with open(self._config_path, encoding="utf-8") as f:
                self._state = yaml.safe_load(f) or {}
        else:
            logger.info("auth config not found at %s — auth disabled", self._config_path)
            self._state = {}
        if bool(self._state.get("enabled", False)) and not self.secrets_valid:
            # 占位符/空 secret 在仓库公开 —— 拒绝以它们运行（防伪造 JWT/直连伪造）。
            # 强制关闭鉴权：mining 侧同步拒占位符 internal_verify_secret → 全链路 401，
            # 运维会立刻发现并改真实 secret。比「带着公开 secret 继续」安全得多。
            logger.critical(
                "auth.yaml enabled=true 但 jwt_secret/internal_verify_secret 缺失或仍是样板占位符 "
                "—— 鉴权强制关闭。请在 auth.yaml 设真实强随机 secret 后 reload。"
            )
        return {
            "enabled": self.enabled,
            "token_ttl_seconds": self.token_ttl_seconds,
            "secrets_valid": self.secrets_valid,
        }

    @property
    def secrets_valid(self) -> bool:
        return _secret_valid(self._state.get("jwt_secret")) and _secret_valid(
            self._state.get("internal_verify_secret")
        )

    @property
    def enabled(self) -> bool:
        # enabled 要求 secrets 有效：占位符/空 secret 时强制视为关闭。
        return bool(self._state.get("enabled", False)) and self.secrets_valid

    @property
    def jwt_secret(self) -> str:
        return str(self._state.get("jwt_secret", ""))

    @property
    def token_ttl_seconds(self) -> int:
        try:
            return int(self._state.get("token_ttl_seconds", 43200))
        except (TypeError, ValueError):
            return 43200

    @property
    def internal_verify_secret(self) -> str:
        return str(self._state.get("internal_verify_secret", ""))

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 暴露内部 secret 给 proxy（app.state 单例，所有请求共享读）。
        # 即使 enabled=False / SKIP_PATHS 也设置，保证 login(SKIP_PATH) 能拿到 secret 调 mining verify。
        request.app.state.internal_verify_secret = self.internal_verify_secret

        if (not self.enabled
                or request.method == "OPTIONS"
                or request.url.path in _SKIP_PATHS
                or _is_open_config_read(request.method, request.url.path)):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse(status_code=401, content={"detail": "unauthenticated"})
        token = auth.split(" ", 1)[1].strip()
        payload = jwt_decode(token, self.jwt_secret)
        if payload is None:
            return JSONResponse(status_code=401, content={"detail": "unauthenticated"})

        request.state.user = {
            "username": payload.get("sub"),
            "role": payload.get("role"),
            "name": payload.get("name"),  # display_name，供 /api/v1/auth/me 回显
        }

        if _is_admin_only(request.method, request.url.path) and payload.get("role") != "admin":
            return JSONResponse(status_code=403, content={"detail": "admin required"})

        return await call_next(request)
