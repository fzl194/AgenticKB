"""JWT 鉴权中间件 —— 校验 Authorization Bearer，强制 admin-only 路径白名单。

身份挂 request.state.user；internal_verify_secret 挂 app.state.internal_verify_secret
（供 proxy._build_forward_headers 注入给 mining）。

默认 fail-closed：auth.yaml 缺失或无 enabled 键 → 中间件不启用（passthrough）。
生产 auth.yaml 显式 enabled: true。
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from main_control_service.jwt_util import decode as jwt_decode

logger = logging.getLogger(__name__)
_PROXY_PREFIX = "/api/v1/proxy/"
SiteAdminValidator = Callable[[Request, str], Awaitable[bool]]

#: 站点管理员实时校验后端（mining）异常时的兜底窗口：窗口内沿用最近一次成功
#: 结论，防 mining 抖动/重启把全部站点管理员锁在控制面外（RBAC 审查 M2）。
#: 正常路径仍每请求实时校验——降权/禁用立即生效的语义不变；0 = 禁用兜底。
SITE_ADMIN_STALE_IF_ERROR_TTL = float(
    os.environ.get("CMKB_SITE_ADMIN_STALE_TTL", "60"))

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
    if method in {"GET", "PUT"} and path.startswith("/api/v1/system"):
        if method == "GET" and path in {"/api/v1/system/ui", "/api/v1/system/ui/raw"}:
            return False
        return True
    if method == "GET" and path == "/api/v1/serving-config":
        return True
    if method in {"POST", "PUT", "DELETE"} and path.startswith("/api/v1/domains"):
        return True
    if method == "GET" and path.startswith("/api/v1/domains/"):
        return True
    if method == "PUT" and "/scenario/raw" in path and path.startswith("/api/v1/domains/"):
        return True
    if method == "GET" and path.startswith("/api/v1/logs"):
        return True
    if path.startswith(_PROXY_PREFIX):
        # Shape: /proxy/{domain}/{service}/{upstream-path}.  Authorization
        # must inspect the upstream path as well as the gateway path, otherwise
        # /llm/api/v1/admin/* and /serving/api/v1/admin/* bypass this guard.
        parts = path[len(_PROXY_PREFIX):].split("/", 2)
        if len(parts) == 3:
            _domain, service, upstream = parts
            if service == "llm":
                return True
            if upstream == "api/v1/admin" or upstream.startswith("api/v1/admin/"):
                return True
            if (service == "serving" and method in {"POST", "PUT", "PATCH", "DELETE"}
                    and (upstream == "api/v1/paradigm"
                         or upstream.startswith("api/v1/paradigm/"))):
                return True
            if (service == "mining" and method in {"POST", "PUT", "PATCH", "DELETE"}
                    and (upstream == "api/mining-workflows"
                         or upstream.startswith("api/mining-workflows/"))):
                return True
    return False


def _proxy_parts(path: str) -> tuple[str, str, str] | None:
    if not path.startswith(_PROXY_PREFIX):
        return None
    parts = path[len(_PROXY_PREFIX):].split("/", 2)
    return tuple(parts) if len(parts) == 3 else None  # type: ignore[return-value]


def _has_unsafe_proxy_path(request: Request) -> bool:
    """Reject path forms that httpx could normalize after authorization."""
    if not request.url.path.startswith(_PROXY_PREFIX):
        return False
    raw = request.scope.get("raw_path", b"")
    raw_text = raw.decode("ascii", "ignore").lower() if isinstance(raw, bytes) else str(raw).lower()
    # Proxy route segments are identifiers/API literals and never require
    # percent-encoding. Reject any encoded byte so repeated decoding by ASGI,
    # httpx and the upstream server cannot change the authorized target.
    if "%" in raw_text or "%" in request.url.path:
        return True
    upstream = _proxy_parts(request.url.path)
    if upstream is None:
        return True
    return any(segment in {".", ".."} or "\\" in segment for segment in upstream[2].split("/"))


def _is_forbidden_proxy_target(path: str) -> bool:
    """Internal-secret endpoints are never exposed through the browser proxy."""
    parts = _proxy_parts(path)
    if parts is None:
        return False
    _domain, service, upstream = parts
    if service == "mining":
        blocked = (
            "api/kb/internal", "api/kb/auth", "api/kb/admin/reload-auth-config",
            "api/kb/mcp-tools",
        )
        return any(upstream == prefix or upstream.startswith(prefix + "/") for prefix in blocked)
    if service == "serving":
        return upstream == "api/internal" or upstream.startswith("api/internal/")
    return False


_PLACEHOLDER_PREFIX = "change-me"


def _secret_valid(v: Any) -> bool:
    return isinstance(v, str) and bool(v) and not v.startswith(_PLACEHOLDER_PREFIX)


def _is_public_config_read(method: str, path: str) -> bool:
    """Only the non-secret UI branding document is public before login."""
    return method == "GET" and path in {
        "/api/v1/system/ui", "/api/v1/system/ui/raw",
    }


def _is_internal_config_read(method: str, path: str) -> bool:
    return method == "GET" and (
        path == "/api/v1/system"
        or path.startswith("/api/v1/system/")
        or path == "/api/v1/serving-config"
    )


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        config_path: Path,
        site_admin_validator: SiteAdminValidator | None = None,
    ) -> None:
        super().__init__(app)
        self._config_path = config_path
        self._state: dict[str, Any] = {}
        self._config_present = False
        self._site_admin_validator = site_admin_validator or self._validate_site_admin
        #: username → (最近一次成功校验的 monotonic 时间, 是否活跃)——仅异常兜底用
        self._admin_verdicts: dict[str, tuple[float, bool]] = {}
        self.reload()

    async def _validate_site_admin(self, request: Request, username: str) -> bool:
        service = getattr(request.app.state, "main_control", None)
        if service is None:
            raise RuntimeError("control plane service unavailable")
        access, reason = await service.domain_access_for(
            username,
            getattr(request.app.state, "internal_verify_secret", "") or "",
        )
        if access is None:
            raise RuntimeError(f"identity backend unavailable:{reason or 'unknown'}")
        return access.get("site_role") == "admin"

    def reload(self) -> dict[str, object]:
        if self._config_path.exists():
            self._config_present = True
            with open(self._config_path, encoding="utf-8") as f:
                self._state = yaml.safe_load(f) or {}
        else:
            self._config_present = False
            logger.critical("auth config not found at %s — refusing requests", self._config_path)
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

        if not self._config_present:
            return JSONResponse(status_code=503, content={"detail": "auth misconfigured"})
        if not self.enabled:
            return await call_next(request)
        if not self.secrets_valid:
            return JSONResponse(status_code=503, content={"detail": "auth misconfigured"})
        if _has_unsafe_proxy_path(request):
            return JSONResponse(status_code=400, content={"detail": "invalid proxy path"})
        if (request.method == "OPTIONS"
                or request.url.path in _SKIP_PATHS
                or _is_public_config_read(request.method, request.url.path)):
            return await call_next(request)

        # 内部服务启动配置读取必须凭共享 secret；loopback/转发头不是身份。
        expected_internal = getattr(request.app.state, "internal_verify_secret", "")
        supplied_internal = request.headers.get("x-internal-auth", "")
        internal_scope = (
            _is_internal_config_read(request.method, request.url.path)
            or (request.method == "GET" and request.url.path.startswith("/api/v1/domains"))
        )
        if (internal_scope and expected_internal and supplied_internal
                and secrets.compare_digest(supplied_internal, expected_internal)):
            request.state.internal_call = True
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

        # A site-admin JWT is only a claimed identity. Re-check the live user
        # row before *any* admin fast path (including ordinary domain proxy
        # requests and the full domain list), so demotion/disable is immediate.
        # 后端异常时仅在 TTL 窗口内沿用最近一次成功结论（stale-if-error）：
        # 语义仍 fail-closed——无缓存/缓存过期 → 503；缓存的 False → 403。
        if payload.get("role") == "admin":
            username = str(payload.get("sub") or "")
            try:
                active_admin = await self._site_admin_validator(request, username)
            except Exception:
                cached = self._admin_verdicts.get(username)
                age = time.monotonic() - cached[0] if cached else None
                if cached is not None and age is not None \
                        and age <= SITE_ADMIN_STALE_IF_ERROR_TTL:
                    logger.warning(
                        "site admin live validation failed — reusing verdict "
                        "from %.1fs ago for %s", age, username,
                    )
                    active_admin = cached[1]
                else:
                    logger.exception("site admin live validation failed")
                    return JSONResponse(
                        status_code=503,
                        content={"detail": "identity backend unavailable"},
                    )
            else:
                self._admin_verdicts[username] = (time.monotonic(), active_admin)
            if not active_admin:
                return JSONResponse(status_code=403, content={"detail": "site admin inactive"})

        if _is_forbidden_proxy_target(request.url.path):
            return JSONResponse(status_code=403, content={"detail": "internal endpoint"})

        if _is_admin_only(request.method, request.url.path):
            if payload.get("role") != "admin":
                return JSONResponse(status_code=403, content={"detail": "admin required"})

        return await call_next(request)
