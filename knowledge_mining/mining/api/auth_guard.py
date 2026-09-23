"""Application-wide authentication guard for the Mining API (P13)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from knowledge_mining.mining.kb.auth import authenticate_request


RequestAuthenticator = Callable[[Request], Awaitable[dict[str, Any]]]

_PUBLIC_ROUTES = frozenset({("GET", "/health")})
_SERVICE_ONLY_ROUTES = frozenset({
    ("POST", "/api/kb/auth/identify"),
    ("POST", "/api/kb/auth/verify"),
    ("POST", "/api/kb/auth/mcp-key-verify"),
    # 批次7：MCP 工具族数据端点（internal-only，路由内自验 X-Internal-Auth）
    ("POST", "/api/kb/mcp-tools/list-kbs"),
    ("POST", "/api/kb/mcp-tools/list-documents"),
    ("POST", "/api/kb/mcp-tools/begin-upload"),
    ("POST", "/api/kb/admin/reload-auth-config"),
})

#: 直传 PUT 是动态票据路径（内部密钥 + 票据 + 用户绑定三重校验在路由内）。
_UPLOAD_DIRECT_PREFIX = "PUT:/api/kb/mcp-tools/upload-direct/"

#: 51号批次1：main_control 域过滤/删域保护的内部只读查询（路由内自验
#: X-Internal-Auth）。带路径参数无法精确枚举，按前缀豁免——该前缀下只有
#: internal_user_domains / internal_kb_count 两个自验端点。遗漏此豁免会让
#: 中间件抢先 401（内网 2026-09-23 实发：登录 verify 放行、查绑定 401）。
_INTERNAL_GET_PREFIX = "GET:/api/kb/internal/"


def _is_exempt(method: str, path: str) -> bool:
    if (method, path) in _PUBLIC_ROUTES | _SERVICE_ONLY_ROUTES:
        return True
    scoped = f"{method}:{path}"
    return scoped.startswith(_UPLOAD_DIRECT_PREFIX) or scoped.startswith(
        _INTERNAL_GET_PREFIX
    )


class MiningApiAuthMiddleware(BaseHTTPMiddleware):
    """Require a trusted gateway identity for every non-exempt ``/api`` route.

    Service-only paths remain responsible for validating their internal secret.
    Resource-level membership and admin checks stay at the existing route layer.
    """

    def __init__(
        self,
        app: Any,
        *,
        authenticate: RequestAuthenticator = authenticate_request,
    ) -> None:
        super().__init__(app)
        self._authenticate = authenticate

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith("/api/"):
            return await call_next(request)
        if _is_exempt(request.method, path):
            return await call_next(request)

        try:
            request.state.authenticated_user = await self._authenticate(request)
        except HTTPException as exc:
            if exc.status_code in {401, 403}:
                return JSONResponse(status_code=401, content={"detail": "unauthenticated"})
            raise
        return await call_next(request)
