from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_control_service.auth import AuthMiddleware
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


def _mw_app(tmp_path: Path, auth_text: str = _AUTH_YAML) -> FastAPI:
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

    app.add_middleware(AuthMiddleware, config_path=auth_path)
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


def test_config_read_open_for_service_pull(tmp_path):
    """mining/serving 启动时拉配置（无用户 token）—— GET /api/v1/system/* 必须开放。"""
    with TestClient(_mw_app(tmp_path)) as c:
        assert c.get("/api/v1/system/cfg/raw").status_code == 200


def test_disabled_middleware_passthrough(tmp_path):
    # enabled:false 必须在构造前写入，否则中间件已按 enabled:true 加载
    with TestClient(_mw_app(tmp_path, auth_text=(
        "enabled: false\njwt_secret: s\ntoken_ttl_seconds: 60\ninternal_verify_secret: ivs\n"
    ))) as c:
        assert c.get("/api/v1/me").status_code == 200


def test_missing_auth_file_disables_auth(tmp_path):
    """auth.yaml 缺失 → fail-closed(disabled) → 既有 system config 测试无需改动即可绿。"""
    app = FastAPI()

    @app.get("/api/v1/me")
    def me():
        return {"u": "r"}

    app.add_middleware(AuthMiddleware, config_path=tmp_path / "system" / "auth.yaml")
    with TestClient(app) as c:
        assert c.get("/api/v1/me").status_code == 200


def test_reload_via_endpoint(tmp_path):
    """POST /api/v1/admin/reload-auth 重新读盘，返回值反映新状态。"""
    from main_control_service.main import create_app
    _write_auth(tmp_path)  # enabled:true
    app = create_app(config_dir=tmp_path)
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
