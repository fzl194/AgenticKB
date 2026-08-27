"""P13 — mining API 默认拒绝与 service-only 白名单回归。"""
from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.routes.auth import _require_internal


def _client(*, authenticated_user: dict[str, Any] | None = None) -> TestClient:
    from knowledge_mining.mining.api.auth_guard import MiningApiAuthMiddleware

    app = FastAPI()

    async def authenticate(request: Request) -> dict[str, Any]:
        if authenticated_user is None:
            raise HTTPException(401, "unauthenticated")
        return authenticated_user

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/legacy-probe")
    async def legacy_probe(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return user

    @app.get("/api/system/status")
    async def system_status() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/kb/auth/identify")
    async def identify(request: Request) -> dict[str, bool]:
        _require_internal(request)
        return {"ok": True}

    @app.post("/api/kb/auth/verify")
    async def verify(request: Request) -> dict[str, bool]:
        _require_internal(request)
        return {"ok": True}

    @app.post("/api/kb/admin/reload-auth-config")
    async def reload_auth(request: Request) -> dict[str, bool]:
        _require_internal(request)
        return {"ok": True}

    app.add_middleware(MiningApiAuthMiddleware, authenticate=authenticate)
    return TestClient(app)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-KB-User": "alice"},
        {"X-KB-User": "alice", "X-Internal-Auth": "wrong"},
    ],
)
def test_default_deny_rejects_unauthenticated_api_requests(headers: dict[str, str]) -> None:
    with _client() as client:
        assert client.get("/api/legacy-probe", headers=headers).status_code == 401


def test_authenticated_request_is_reused_by_current_user() -> None:
    user = {"id": "u-1", "username": "alice", "site_role": "member"}
    with _client(authenticated_user=user) as client:
        response = client.get(
            "/api/legacy-probe",
            headers={"X-KB-User": "alice", "X-Internal-Auth": "correct"},
        )

    assert response.status_code == 200
    assert response.json() == user


def test_only_health_is_anonymous_public_endpoint() -> None:
    with _client() as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/system/status").status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/kb/auth/identify",
        "/api/kb/auth/verify",
        "/api/kb/admin/reload-auth-config",
    ],
)
def test_service_only_paths_bypass_user_authentication(path: str) -> None:
    with _client() as client:
        response = client.post(path, headers={"X-Internal-Auth": "test-ivs"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Internal-Auth": "wrong"}],
)
def test_service_only_paths_still_require_the_internal_secret(headers: dict[str, str]) -> None:
    with _client() as client:
        response = client.post("/api/kb/admin/reload-auth-config", headers=headers)
    assert response.status_code == 401


def test_service_only_whitelist_is_method_specific() -> None:
    with _client() as client:
        assert client.get("/api/kb/auth/verify").status_code == 401


def test_service_only_internal_auth_precedes_body_validation() -> None:
    """缺 body + 无内部 secret 必须 401（鉴权先于 422，不泄露参数 schema）。"""
    from knowledge_mining.mining.api.app import create_app

    app = create_app()
    app.state.pg_pool = object()  # 依赖解析需要；鉴权短路后不会真正触库
    client = TestClient(app)
    try:
        for path in ("/api/kb/auth/identify", "/api/kb/auth/verify"):
            response = client.post(path)
            assert response.status_code == 401, path
    finally:
        client.close()


def test_service_only_body_validation_applies_after_internal_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """鉴权通过后缺 body 仍应 422（body 校验不因鉴权前置而丢失）。"""
    from knowledge_mining.mining.api.app import create_app
    from knowledge_mining.mining.kb.routes import auth as routes_auth

    monkeypatch.setattr(routes_auth, "get_internal_verify_secret", lambda: "test-ivs")
    app = create_app()
    app.state.pg_pool = object()  # KbDB 惰性持有；body 校验不触库
    client = TestClient(app)
    try:
        response = client.post(
            "/api/kb/auth/verify",
            headers={"X-Internal-Auth": "test-ivs"},
        )
        assert response.status_code == 422
    finally:
        client.close()


@pytest.mark.parametrize(
    "path",
    [
        "/api/knowledge/stats",
        "/api/builds",
        "/api/ontology/versions",
        "/api/mining-workflows",
        "/api/knowledge/documents/document-1/download",
        "/api/config",
        "/api/system/status",
    ],
)
def test_real_app_rejects_anonymous_legacy_routes(path: str) -> None:
    from knowledge_mining.mining.api.app import create_app

    client = TestClient(create_app())
    try:
        assert client.get(path).status_code == 401
    finally:
        client.close()


def test_real_app_cors_allows_only_configured_local_origin() -> None:
    from knowledge_mining.mining.api.app import create_app

    client = TestClient(create_app())
    try:
        allowed = client.options(
            "/api/knowledge/stats",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "GET",
            },
        )
        blocked = client.options(
            "/api/knowledge/stats",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    finally:
        client.close()

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:8080"
    assert blocked.status_code == 400
    assert "access-control-allow-origin" not in blocked.headers


def test_real_app_uses_fixed_non_wildcard_credentialed_cors() -> None:
    from knowledge_mining.mining.api.app import _cors_origins

    assert _cors_origins() == ["http://localhost:8080", "http://127.0.0.1:8080"]


def test_real_app_disables_unauthenticated_api_documentation() -> None:
    from knowledge_mining.mining.api.app import create_app

    app = create_app()
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_every_non_exempt_api_route_rejects_anonymous_requests() -> None:
    from knowledge_mining.mining.api.app import create_app
    from knowledge_mining.mining.api.auth_guard import _is_exempt

    app = create_app()
    client = TestClient(app)
    try:
        protected = [
            route
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path.startswith("/api/")
            and not any(_is_exempt(method, route.path) for method in route.methods or ())
        ]
        assert protected
        for route in protected:
            method = next(method for method in route.methods or () if method not in {"HEAD", "OPTIONS"})
            path = re.sub(r"\{[^}]+\}", "probe", route.path)
            assert client.request(method, path).status_code == 401, f"{method} {route.path}"
    finally:
        client.close()
