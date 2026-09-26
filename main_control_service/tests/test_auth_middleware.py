from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_control_service.auth import AuthMiddleware, _has_unsafe_proxy_path
from main_control_service.jwt_util import encode

_AUTH_YAML = """\
enabled: true
jwt_secret: test-secret
token_ttl_seconds: 3600
internal_verify_secret: test-ivs
bootstrap:
  admin_password: initpass
"""


def _write_auth(tmp_path: Path, text: str = _AUTH_YAML) -> Path:
    auth_path = tmp_path / "system" / "auth.yaml"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(text, encoding="utf-8")
    return auth_path


def _mw_app(
    tmp_path: Path,
    auth_text: str = _AUTH_YAML,
    *,
    site_admin_active: bool = True,
) -> FastAPI:
    """最小 app：只挂 AuthMiddleware + 几个探测路由。auth_text 在构造前写入。"""
    auth_path = _write_auth(tmp_path, auth_text)
    app = FastAPI()

    @app.get("/health")
    def h():
        return {"ok": 1}

    @app.get("/api/v1/auth/login")
    def login():
        return {"token": "x"}

    @app.get("/api/v1/me")
    def me():
        return {"u": "r"}

    @app.put("/api/v1/system/cfg/raw")
    def put_cfg():
        return {"ok": 1}

    @app.get("/api/v1/system/cfg/raw")
    def get_cfg():
        return {"ok": 1}

    @app.get("/api/v1/domains")
    def list_domains():
        return {"items": []}

    @app.api_route(
        "/api/v1/proxy/{domain}/{service}/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    def proxy_probe():
        return {"ok": 1}

    async def validate_site_admin(_request, _username):
        return site_admin_active

    app.add_middleware(
        AuthMiddleware,
        config_path=auth_path,
        site_admin_validator=validate_site_admin,
    )
    return app


def _token(role: str, secret: str = "test-secret") -> str:
    return encode({"sub": "u1", "role": role, "name": "U"}, secret, ttl=3600)


def test_skip_paths_no_token(tmp_path):
    with TestClient(_mw_app(tmp_path)) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/api/v1/auth/login").status_code == 200


def test_missing_token_401(tmp_path):
    with TestClient(_mw_app(tmp_path)) as c:
        assert c.get("/api/v1/me").status_code == 401


def test_valid_token_passes(tmp_path):
    with TestClient(_mw_app(tmp_path)) as c:
        r = c.get("/api/v1/me", headers={"Authorization": f"Bearer {_token('member')}"})
        assert r.status_code == 200


def test_expired_token_401(tmp_path):
    token = encode({"sub": "u", "role": "member", "name": "U"}, "test-secret", ttl=-5)
    with TestClient(_mw_app(tmp_path)) as c:
        assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_admin_only_path_member_403(tmp_path):
    with TestClient(_mw_app(tmp_path)) as c:
        r = c.put("/api/v1/system/cfg/raw", headers={"Authorization": f"Bearer {_token('member')}"})
        assert r.status_code == 403


def test_admin_only_path_admin_ok(tmp_path):
    with TestClient(_mw_app(tmp_path)) as c:
        r = c.put("/api/v1/system/cfg/raw", headers={"Authorization": f"Bearer {_token('admin')}"})
        assert r.status_code == 200


def test_stale_admin_jwt_is_rejected_after_database_demotion(tmp_path):
    with TestClient(_mw_app(tmp_path, site_admin_active=False)) as c:
        sensitive = c.put(
            "/api/v1/system/cfg/raw",
            headers={"Authorization": f"Bearer {_token('admin')}"},
        )
        ordinary_proxy = c.get(
            "/api/v1/proxy/generic/mining/api/kb/overview",
            headers={"Authorization": f"Bearer {_token('admin')}"},
        )
    assert sensitive.status_code == 403
    assert ordinary_proxy.status_code == 403
    assert sensitive.json()["detail"] == "site admin inactive"


def test_proxy_inner_admin_path_requires_site_admin(tmp_path):
    path = "/api/v1/proxy/generic/llm/api/v1/admin/reload-config"
    with TestClient(_mw_app(tmp_path)) as c:
        member = c.post(path, headers={"Authorization": f"Bearer {_token('member')}"})
        admin = c.post(path, headers={"Authorization": f"Bearer {_token('admin')}"})
    assert member.status_code == 403
    assert admin.status_code == 200


def test_proxy_global_paradigm_write_requires_site_admin(tmp_path):
    path = "/api/v1/proxy/generic/serving/api/v1/paradigm/p-1/publish"
    with TestClient(_mw_app(tmp_path)) as c:
        member = c.post(path, headers={"Authorization": f"Bearer {_token('member')}"})
        admin = c.post(path, headers={"Authorization": f"Bearer {_token('admin')}"})
    assert member.status_code == 403
    assert admin.status_code == 200


def test_proxy_regular_domain_request_stays_available_to_member(tmp_path):
    path = "/api/v1/proxy/generic/mining/api/kb/overview"
    with TestClient(_mw_app(tmp_path)) as c:
        response = c.get(path, headers={"Authorization": f"Bearer {_token('member')}"})
    assert response.status_code == 200


def test_proxy_internal_only_route_is_never_browser_callable(tmp_path):
    path = "/api/v1/proxy/generic/mining/api/kb/internal/users/alice/domain-access"
    with TestClient(_mw_app(tmp_path)) as c:
        member = c.get(path, headers={"Authorization": f"Bearer {_token('member')}"})
        admin = c.get(path, headers={"Authorization": f"Bearer {_token('admin')}"})
    assert member.status_code == 403
    assert admin.status_code == 403


def test_proxy_rejects_encoded_dot_segments_before_path_policy() -> None:
    from starlette.requests import Request

    request = Request({
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "root_path": "",
        "path": "/api/v1/proxy/generic/serving/api/v1/foo/../admin/reload-config",
        "raw_path": b"/api/v1/proxy/generic/serving/api/v1/foo/%2e%2e/admin/reload-config",
        "query_string": b"",
        "headers": [],
    })

    assert _has_unsafe_proxy_path(request) is True


def test_proxy_rejects_double_encoded_separator() -> None:
    from starlette.requests import Request

    request = Request({
        "type": "http", "method": "GET", "scheme": "http",
        "server": ("test", 80), "client": ("127.0.0.1", 1234),
        "root_path": "",
        "path": "/api/v1/proxy/generic/mining/api/kb%2finternal/users/alice",
        "raw_path": b"/api/v1/proxy/generic/mining/api/kb%252finternal/users/alice",
        "query_string": b"", "headers": [],
    })

    assert _has_unsafe_proxy_path(request) is True


def test_config_read_from_forwarded_client_requires_auth(tmp_path):
    """Nginx 转发的匿名请求不得读取原始系统配置。"""
    with TestClient(_mw_app(tmp_path), client=("127.0.0.1", 50000)) as c:
        response = c.get(
            "/api/v1/system/cfg/raw",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
    assert response.status_code == 401


def test_config_read_direct_loopback_requires_internal_secret(tmp_path):
    """loopback 不是身份；同容器调用也必须提供共享内部凭证。"""
    with TestClient(_mw_app(tmp_path), client=("127.0.0.1", 50000)) as c:
        assert c.get("/api/v1/system/cfg/raw").status_code == 401
        assert c.get(
            "/api/v1/system/cfg/raw",
            headers={"X-Internal-Auth": "test-ivs"},
        ).status_code == 200


def test_serving_config_requires_internal_secret_or_site_admin(tmp_path):
    app = _mw_app(tmp_path)

    @app.get("/api/v1/serving-config")
    def serving_config():
        return {"domains": {}}

    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        assert c.get("/api/v1/serving-config").status_code == 401
        assert c.get(
            "/api/v1/serving-config",
            headers={"X-Internal-Auth": "test-ivs"},
        ).status_code == 200
        assert c.get(
            "/api/v1/serving-config",
            headers={"Authorization": f"Bearer {_token('member')}"},
        ).status_code == 403
        assert c.get(
            "/api/v1/serving-config",
            headers={"Authorization": f"Bearer {_token('admin')}"},
        ).status_code == 200


def test_config_read_member_forbidden_but_admin_allowed(tmp_path):
    """浏览器配置管理面仅站点管理员可读 raw YAML。"""
    forwarded = {"X-Forwarded-For": "203.0.113.9"}
    with TestClient(_mw_app(tmp_path), client=("127.0.0.1", 50000)) as c:
        member = c.get(
            "/api/v1/system/cfg/raw",
            headers={**forwarded, "Authorization": f"Bearer {_token('member')}"},
        )
        admin = c.get(
            "/api/v1/system/cfg/raw",
            headers={**forwarded, "Authorization": f"Bearer {_token('admin')}"},
        )
    assert member.status_code == 403
    assert admin.status_code == 200


def test_disabled_middleware_passthrough(tmp_path):
    # enabled:false 必须在构造前写入，否则中间件已按 enabled:true 加载
    with TestClient(_mw_app(tmp_path, auth_text=(
        "enabled: false\njwt_secret: s\ntoken_ttl_seconds: 60\ninternal_verify_secret: ivs\n"
    ))) as c:
        assert c.get("/api/v1/me").status_code == 200


def test_missing_auth_file_fails_closed(tmp_path):
    """auth.yaml 缺失时不得退化为全站匿名放行。"""
    app = FastAPI()

    @app.get("/api/v1/me")
    def me():
        return {"u": "r"}

    app.add_middleware(AuthMiddleware, config_path=tmp_path / "system" / "auth.yaml")
    with TestClient(app) as c:
        assert c.get("/api/v1/me").status_code == 503


def test_reload_via_endpoint(tmp_path):
    """POST /api/v1/admin/reload-auth 重新读盘，返回值反映新状态。"""
    from main_control_service.main import create_app
    _write_auth(tmp_path)  # enabled:true
    app = create_app(config_dir=tmp_path)
    with patch(
        "main_control_service.service.YamlConfigService.domain_access_for",
        new_callable=AsyncMock,
    ) as access:
        access.return_value = ({"site_role": "admin", "grants": []}, "")
        with TestClient(app) as c:
            admin = {"Authorization": f"Bearer {_token('admin')}"}
            # 初始 enabled=true
            assert c.post("/api/v1/admin/reload-auth", headers=admin).json()["enabled"] is True
            # 改文件为 disabled
            _write_auth(tmp_path, "enabled: false\njwt_secret: s2\ntoken_ttl_seconds: 60\ninternal_verify_secret: ivs2\n")
            r = c.post("/api/v1/admin/reload-auth", headers=admin)
            assert r.status_code == 200, r.text
            assert r.json()["enabled"] is False


def test_placeholder_secrets_fail_closed(tmp_path):
    """jwt_secret/internal_verify_secret 仍是样板占位符时，启用的认证不能退化为放行。"""
    with TestClient(_mw_app(tmp_path, auth_text=(
        "enabled: true\n"
        "jwt_secret: change-me-to-a-strong-random-32byte-hex\n"
        "token_ttl_seconds: 3600\n"
        "internal_verify_secret: change-me-internal-verify-secret\n"
    ))) as c:
        assert c.get("/api/v1/me").status_code == 503


def test_empty_control_plane_secrets_fail_closed(tmp_path):
    auth_text = (
        "enabled: true\n"
        "jwt_secret: ''\n"
        "internal_verify_secret: ''\n"
    )
    with TestClient(_mw_app(tmp_path, auth_text=auth_text)) as c:
        assert c.get("/api/v1/me").status_code == 503


def test_cors_preflight_allows_only_configured_origin_before_auth(tmp_path):
    """CORS 在 Auth 之外，且只接受受信任 UI 来源的 preflight。"""
    from main_control_service.main import create_app
    _write_auth(tmp_path)
    app = create_app(config_dir=tmp_path)
    with TestClient(app) as c:
        allowed = c.options("/api/v1/auth/me", headers={
            "Origin": "http://localhost:8080",
            "Access-Control-Request-Method": "GET",
        })
        blocked = c.options("/api/v1/auth/me", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        })
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:8080"
    assert blocked.status_code == 400
    assert "access-control-allow-origin" not in blocked.headers


# ----------------------------------------------------------------------
# 51号批次1：/api/v1/domains 读收口 —— 内部 secret 旁路（fail-closed）
# ----------------------------------------------------------------------

def test_domains_read_requires_token_or_internal_secret(tmp_path):
    """收口后无 token 无内部头 → 401（原为免鉴权可读）。"""
    with TestClient(_mw_app(tmp_path)) as c:
        assert c.get("/api/v1/domains").status_code == 401


def test_domains_read_internal_bypass_with_correct_secret(tmp_path):
    """无 token + 正确 X-Internal-Auth → 放行（服务启动期拉取）。"""
    with TestClient(_mw_app(tmp_path)) as c:
        r = c.get("/api/v1/domains", headers={"X-Internal-Auth": "test-ivs"})
        assert r.status_code == 200


def test_domains_read_wrong_internal_secret_401(tmp_path):
    """无 token + 错误 X-Internal-Auth → 401（fail-closed）。"""
    with TestClient(_mw_app(tmp_path)) as c:
        assert c.get("/api/v1/domains", headers={"X-Internal-Auth": "wrong"}).status_code == 401


def test_domains_read_no_secret_configured_fails_closed(tmp_path):
    """internal_verify_secret 未配置（空）时，secret 匹配旁路不生效（fail-closed）。

    注意：此配置下 secrets_valid=False → 中间件先 503；无论哪种，都不是 200 放行。
    """
    with TestClient(_mw_app(tmp_path, auth_text=(
        "enabled: true\njwt_secret: s\ntoken_ttl_seconds: 60\ninternal_verify_secret: ''\n"
    ))) as c:
        r = c.get("/api/v1/domains", headers={"X-Internal-Auth": ""})
        assert r.status_code != 200


def test_domains_write_not_bypassed_by_internal_secret(tmp_path):
    """内部旁路仅限 GET —— POST /api/v1/domains 不旁路（admin-only 且需 token）。"""
    with TestClient(_mw_app(tmp_path)) as c:
        r = c.post("/api/v1/domains", headers={"X-Internal-Auth": "test-ivs"})
        assert r.status_code == 401


def test_domain_config_detail_is_site_admin_only(tmp_path):
    """域详情/raw 含 database/services，只能由站点管理员读取。"""
    with TestClient(_mw_app(tmp_path)) as c:
        member_headers = {"Authorization": f"Bearer {_token('member')}"}
        assert c.get("/api/v1/domains/generic", headers=member_headers).status_code == 403
        assert c.get("/api/v1/domains/generic/raw", headers=member_headers).status_code == 403
