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
    ("POST", "/api/kb/admin/reload-auth-config"),
})


def _is_exempt(method: str, path: str) -> bool:
    return (method, path) in _PUBLIC_ROUTES | _SERVICE_ONLY_ROUTES


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
