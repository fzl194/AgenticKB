"""P13 — deployment surface and credential configuration regressions."""
from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_sensitive_service_ports_bind_to_loopback_only() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    app = compose["services"]["app"]
    ports = app["ports"]

    for container_port in (8900, 8901, 8081, 8910, 9000):
        assert any(
            str(port).startswith("127.0.0.1:") and str(port).endswith(f":{container_port}")
            for port in ports
        )

    assert {
        "CMKB_JWT_SECRET",
        "CMKB_INTERNAL_VERIFY_SECRET",
        "CMKB_BOOTSTRAP_ADMIN_PASSWORD",
        "CMKB_CORS_ORIGINS",
    } <= set(app["environment"])


def test_tracked_auth_config_references_environment_variables_only() -> None:
    auth_path = REPO_ROOT / "main_control_service" / "config" / "system" / "auth.yaml"
    auth = yaml.safe_load(auth_path.read_text(encoding="utf-8"))

    assert auth.get("jwt_secret_env") == "CMKB_JWT_SECRET"
    assert auth.get("internal_verify_secret_env") == "CMKB_INTERNAL_VERIFY_SECRET"
    assert auth.get("bootstrap", {}).get("admin_password_env") == "CMKB_BOOTSTRAP_ADMIN_PASSWORD"
    assert "jwt_secret" not in auth
    assert "internal_verify_secret" not in auth
    assert "admin_password" not in auth.get("bootstrap", {})
