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

def test_tracked_auth_config_is_a_secret_free_initialization_template() -> None:
    """Git 跟踪的 auth.yaml 必须是无凭据模板。

    部署宿主机的本地副本会被 deploy-server.sh 按设计写入真实凭据（属预期脏文件），
    因此这里读 HEAD 版本而非工作区文件；无 git 环境（如裸 CI）则跳过。
    """
    import subprocess

    result = subprocess.run(
        ["git", "show", "HEAD:main_control_service/config/system/auth.yaml"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        import pytest

        pytest.skip("git 不可用，无法校验 HEAD 版 auth.yaml")
    auth = yaml.safe_load(result.stdout)

    assert auth.get("jwt_secret") == ""
    assert auth.get("internal_verify_secret") == ""
    assert auth.get("bootstrap", {}).get("admin_password") == ""
    assert auth.get("bootstrap", {}).get("initialize_on_deploy") is True
    assert "jwt_secret_env" not in auth
    assert "internal_verify_secret_env" not in auth


def test_deploy_script_initializes_control_plane_auth_config_once_with_restrictive_permissions() -> None:
    script = (REPO_ROOT / "deploy-server.sh").read_text(encoding="utf-8")

    assert "ensure_auth_config_secrets()" in script
    assert "AUTH_CONFIG_PATH" in script
    assert "initialize_on_deploy" in script
    assert "jwt_secret" in script
    assert "internal_verify_secret" in script
    assert "admin_password" in script
    assert "chmod 600" in script
    assert 'AUTH_CONFIG_PATH="main_control_service/config/system/auth.yaml"' in script
    assert '[ -L "$AUTH_CONFIG_PATH" ]' in script

    apply_config_only = script[script.index("apply_config_only()"):script.index("deploy_from_image()")]
    assert apply_config_only.index("require_host_config") < apply_config_only.index("ensure_auth_config_secrets")

    deploy_from_image = script[script.index("deploy_from_image()"):script.index("FORCE=false")]
    assert deploy_from_image.rindex("require_host_config") < deploy_from_image.rindex("ensure_auth_config_secrets")
    assert deploy_from_image.rindex("ensure_auth_config_secrets") < deploy_from_image.rindex("start_and_verify")
