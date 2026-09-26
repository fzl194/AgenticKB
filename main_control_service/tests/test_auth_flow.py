from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main_control_service.jwt_util import encode
from main_control_service.main import create_app
from main_control_service.proxy import _build_forward_headers

_AUTH = (
    "enabled: true\njwt_secret: s\ntoken_ttl_seconds: 3600\n"
    "internal_verify_secret: ivs\nbootstrap: {admin_password: x}\n"
)


def _client(tmp_path: Path, *, registry: str | None = None) -> TestClient:
    d = tmp_path / "system"
    d.mkdir(parents=True, exist_ok=True)
    (d / "auth.yaml").write_text(_AUTH, encoding="utf-8")
    # login 端点要遍历 list_domains 找 mining_url —— 写一份最小 registry
    (tmp_path / "domain_registry.yaml").write_text(
        registry or (
            "default_domain: d\ndomains:\n  d:\n    display_name: D\n    enabled: true\n"
            "    services:\n      mining_url: http://mining:8901\n"
        ),
        encoding="utf-8",
    )
    return TestClient(create_app(config_dir=tmp_path))


def test_login_success_returns_token(tmp_path):
    with _client(tmp_path) as c:
        with patch("main_control_service.main.verify_user_via_mining", new_callable=AsyncMock) as m:
            m.return_value = {"ok": True, "user": {"username": "alice", "display_name": "Alice", "site_role": "admin"}}
            r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "pw"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert "token" in body and body["token"].count(".") == 2
            assert body["user"]["username"] == "alice"
            assert body["user"]["site_role"] == "admin"


def test_login_bad_credentials_401(tmp_path):
    with _client(tmp_path) as c:
        with patch("main_control_service.main.verify_user_via_mining", new_callable=AsyncMock) as m:
            m.return_value = None
            r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "bad"})
            assert r.status_code == 401


def test_login_missing_fields_400(tmp_path):
    with _client(tmp_path) as c:
        r = c.post("/api/v1/auth/login", json={"username": ""})
        assert r.status_code == 400


def test_me_returns_claims(tmp_path):
    with _client(tmp_path) as c:
        token = encode({"sub": "alice", "role": "member", "name": "Alice"}, "s", ttl=3600)
        r = c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == "alice"
        assert body["site_role"] == "member"
        assert body["display_name"] == "Alice"


def test_me_without_token_401(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/api/v1/auth/me").status_code == 401


def test_member_domain_filter_preserves_display_name(tmp_path):
    registry = (
        "default_domain: cloud_core_network\ndomains:\n"
        "  cloud_core_network:\n    display_name: Cloud Core Network\n    enabled: true\n"
        "    services:\n      mining_url: http://mining:8901\n"
        "  domain_a:\n    display_name: Domain A\n    enabled: true\n"
        "    services:\n      mining_url: http://mining:8901\n"
    )
    token = encode({"sub": "alice", "role": "member", "name": "Alice"}, "s", ttl=3600)
    with _client(tmp_path, registry=registry) as c:
        with patch(
            "main_control_service.service.YamlConfigService.domain_access_for",
            new_callable=AsyncMock,
        ) as access:
            access.return_value = ({
                "site_role": "member",
                "grants": [{"domain": "domain_a", "domain_role": "admin"}],
                "capabilities_by_domain": {
                    "domain_a": ["domain.users.manage", "domain.kbs.manage"],
                },
            }, "")
            r = c.get("/api/v1/domains", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    assert r.json()["items"] == [{
        "domain_id": "domain_a",
        "display_name": "Domain A",
        "enabled": True,
        "default_channel": "prod",
        "scenario_pack_ref": "domain_a",
        "domain_role": "admin",
        "capabilities": ["domain.users.manage", "domain.kbs.manage"],
    }]


def test_member_proxy_is_limited_to_assigned_domains(tmp_path):
    registry = (
        "default_domain: domain_a\ndomains:\n"
        "  domain_a:\n    display_name: Domain A\n    enabled: true\n"
        "    services:\n      mining_url: http://mining:8901\n"
        "  domain_b:\n    display_name: Domain B\n    enabled: true\n"
        "    services:\n      mining_url: http://mining:8901\n"
    )
    token = encode({"sub": "alice", "role": "member", "name": "Alice"}, "s", ttl=3600)
    with _client(tmp_path, registry=registry) as c:
        with patch(
            "main_control_service.service.YamlConfigService.domain_access_for",
            new_callable=AsyncMock,
        ) as access:
            access.return_value = ({
                "site_role": "member",
                "grants": [{"domain": "domain_a", "domain_role": "admin"}],
                "capabilities_by_domain": {"domain_a": ["domain.kbs.manage"]},
            }, "")
            with patch(
                "main_control_service.main.proxy_request",
                new_callable=AsyncMock,
            ) as proxy:
                from fastapi.responses import JSONResponse
                proxy.return_value = JSONResponse({"ok": True})
                allowed = c.get(
                    "/api/v1/proxy/domain_a/mining/api/kb/overview",
                    headers={"Authorization": f"Bearer {token}"},
                )
            denied = c.get(
                "/api/v1/proxy/domain_b/mining/api/kb/overview",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert allowed.status_code == 200
    assert denied.status_code == 404


def test_identify_returns_mode(tmp_path):
    with _client(tmp_path) as c:
        with patch("main_control_service.main.identify_via_mining", new_callable=AsyncMock) as m:
            m.return_value = {"mode": "password"}
            r = c.post("/api/v1/auth/identify", json={"username": "root"})
            assert r.status_code == 200, r.text
            assert r.json()["mode"] == "password"


def test_login_member_no_password(tmp_path):
    """工号 member 无密码也能登录（password 可空）。"""
    with _client(tmp_path) as c:
        with patch("main_control_service.main.verify_user_via_mining", new_callable=AsyncMock) as m:
            m.return_value = {"ok": True, "user": {"username": "alice", "display_name": "Alice", "site_role": "member"}}
            r = c.post("/api/v1/auth/login", json={"username": "alice"})  # 无 password
            assert r.status_code == 200, r.text
            assert r.json()["user"]["site_role"] == "member"


def test_build_forward_headers_injects_kb_headers():
    """有 request.state.user 时，转发头注入 X-KB-User/X-KB-Role/X-Internal-Auth。"""
    req = SimpleNamespace(
        headers={"x-some-header": "keep"},
        client=SimpleNamespace(host="1.2.3.4"),
        url=SimpleNamespace(scheme="http"),
        state=SimpleNamespace(user={"username": "alice", "role": "member"}),
        app=SimpleNamespace(state=SimpleNamespace(internal_verify_secret="ivs")),
    )
    h = _build_forward_headers(req)
    assert h["X-KB-User"] == "alice"
    assert h["X-KB-Role"] == "member"
    assert h["X-Internal-Auth"] == "ivs"
    assert h["x-some-header"] == "keep"
    assert h["X-Forwarded-For"] == "1.2.3.4"


def test_build_forward_headers_no_user_skips_injection():
    """无 request.state.user（如 SKIP_PATH 的 login）→ 不注入 X-KB-*。"""
    req = SimpleNamespace(
        headers={},
        client=SimpleNamespace(host="1.2.3.4"),
        url=SimpleNamespace(scheme="http"),
        state=SimpleNamespace(),  # 无 user
        app=SimpleNamespace(state=SimpleNamespace(internal_verify_secret="ivs")),
    )
    h = _build_forward_headers(req)
    assert "X-KB-User" not in h
    assert "X-Internal-Auth" not in h


def test_build_forward_headers_strips_authorization():
    """浏览器自带的 Authorization 必须被剥（不转发给 mining）。"""
    req = SimpleNamespace(
        headers={"authorization": "Bearer jwt-secret", "cookie": "c"},
        client=SimpleNamespace(host="1.2.3.4"),
        url=SimpleNamespace(scheme="http"),
        state=SimpleNamespace(user={"username": "a", "role": "member"}),
        app=SimpleNamespace(state=SimpleNamespace(internal_verify_secret="ivs")),
    )
    h = _build_forward_headers(req)
    assert "authorization" not in h and "Authorization" not in h
    assert "cookie" not in h
    assert h["X-KB-User"] == "a"
