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
    if method == "GET" and path.startswith("/api/v1/logs/"):
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
        return {
            "enabled": self.enabled,
            "token_ttl_seconds": self.token_ttl_seconds,
        }

    @property
    def enabled(self) -> bool:
        return bool(self._state.get("enabled", False))

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

        if not self.enabled or request.method == "OPTIONS" or request.url.path in _SKIP_PATHS:
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
