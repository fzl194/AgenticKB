"""Reverse-proxy module — streams requests to per-domain backend services."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger(__name__)

# Service key → field name in domain_registry.yaml services section
SERVICE_MAP: dict[str, str] = {
    "llm": "llm_url",
    "mining": "mining_url",
    "serving": "serving_url",
    "eval": "eval_url",
}

# Headers stripped before forwarding to backend (hop-by-hop + sensitive)
_STRIP_REQUEST_HEADERS = frozenset({
    "host", "content-length", "connection", "keep-alive",
    "transfer-encoding", "upgrade", "proxy-connection",
    "proxy-authenticate", "proxy-authorization",
    "cookie", "authorization",
})

# Cloud metadata endpoint — the only blocked network.
# Private/loopback IPs are intentionally allowed because this proxy's
# purpose is to reach internal backend services.
_METADATA_NETWORK = ipaddress.ip_network("169.254.169.254/32")

# Shared client — created once in lifespan, reused across requests.
_proxy_client: httpx.AsyncClient | None = None


def create_proxy_client() -> httpx.AsyncClient:
    """Call once during app lifespan."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        follow_redirects=False,
        proxy=None,
        trust_env=False,
    )


def get_proxy_client() -> httpx.AsyncClient:
    if _proxy_client is None:
        raise RuntimeError("Proxy client not initialized — call init in lifespan")
    return _proxy_client


def set_proxy_client(client: httpx.AsyncClient) -> None:
    global _proxy_client
    _proxy_client = client


async def shutdown_proxy_client() -> None:
    global _proxy_client
    if _proxy_client is not None:
        await _proxy_client.aclose()
        _proxy_client = None


def _is_metadata_host(hostname: str) -> bool:
    """Return True if hostname resolves to the cloud metadata endpoint."""
    try:
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _type, _proto, _canon, sockaddr in addrinfos:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip in _METADATA_NETWORK:
                return True
    except socket.gaierror:
        return False
    return False


def _validate_url(url: str) -> None:
    """Reject non-http(s) schemes and cloud metadata IPs to prevent SSRF."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid backend URL scheme")
    hostname = parsed.hostname
    if hostname and _is_metadata_host(hostname):
        raise HTTPException(status_code=400, detail="Backend URL resolves to cloud metadata endpoint")


def _resolve_target_url(domain_services: dict, service: str) -> str:
    """Look up the internal URL for a (domain, service) pair."""
    field = SERVICE_MAP.get(service)
    if field is None:
        raise HTTPException(status_code=400, detail=f"Unknown service: {service}")
    url = domain_services.get(field)
    if not url:
        raise HTTPException(status_code=502, detail=f"Service {service} not configured for this domain")
    url = url.rstrip("/")
    _validate_url(url)
    return url


def _build_forward_headers(request: Request) -> dict[str, str]:
    """Strip hop-by-hop/sensitive, add proxy context + gateway-injected identity."""
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _STRIP_REQUEST_HEADERS
    }
    # Append to existing X-Forwarded-For if present
    existing_xff = request.headers.get("x-forwarded-for")
    client_ip = request.client.host if request.client else "unknown"
    headers["X-Forwarded-For"] = f"{existing_xff}, {client_ip}" if existing_xff else client_ip
    headers["X-Forwarded-Proto"] = request.url.scheme

    # Phase 2：AuthMiddleware 已把身份挂 request.state.user；反代把派生头注入给 mining。
    # X-KB-User/X-KB-Role/X-Internal-Auth 都不在 _STRIP_REQUEST_HEADERS，会被转发；
    # 浏览器自带的 Authorization 仍被剥。mining 的 current_user 校验 X-Internal-Auth。
    user = getattr(request.state, "user", None)
    if user:
        headers["X-KB-User"] = str(user.get("username", ""))
        headers["X-KB-Role"] = str(user.get("role", ""))
        ivs = getattr(request.app.state, "internal_verify_secret", "") or ""
        if ivs:
            headers["X-Internal-Auth"] = ivs
    return headers


async def proxy_request(
    request: Request,
    domain_id: str,
    service: str,
    path: str,
    domain_services: dict,
) -> Response:
    """Stream a request to the target backend and stream the response back."""
    target_base = _resolve_target_url(domain_services, service)
    target_url = f"{target_base}/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    client = get_proxy_client()
    headers = _build_forward_headers(request)
    body = await request.body()

    upstream_cm = client.stream(
        method=request.method,
        url=target_url,
        headers=headers,
        content=body,
    )
    try:
        resp = await upstream_cm.__aenter__()
    except httpx.ConnectError as exc:
        logger.warning("Proxy connect error for %s/%s: %s", domain_id, service, exc)
        raise HTTPException(status_code=502, detail="Backend service unavailable") from exc
    except httpx.TimeoutException as exc:
        logger.warning("Proxy timeout for %s/%s: %s", domain_id, service, exc)
        raise HTTPException(status_code=504, detail="Backend service timeout") from exc
    except Exception as exc:
        logger.exception("Proxy error for %s/%s", domain_id, service)
        raise HTTPException(status_code=502, detail="Backend service error") from exc

    # Stream response back — always close upstream connection in finally
    closed = False

    async def _stream():
        nonlocal closed
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            if not closed:
                closed = True
                await upstream_cm.__aexit__(None, None, None)

    # Filter out hop-by-hop headers
    response_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in ("transfer-encoding", "connection", "keep-alive")
    }

    return StreamingResponse(
        _stream(),
        status_code=resp.status_code,
        headers=response_headers,
        media_type=resp.headers.get("content-type"),
    )
