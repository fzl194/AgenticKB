from pathlib import Path

from main_control_service.internal_auth import load_control_plane_internal_auth_secret


def test_env_secret_takes_priority_over_auth_yaml(tmp_path: Path) -> None:
    auth = tmp_path / "auth.yaml"
    auth.write_text("internal_verify_secret: file-secret\n", encoding="utf-8")
    assert load_control_plane_internal_auth_secret(
        environ={"CONTROL_PLANE_INTERNAL_AUTH_SECRET": "env-secret"},
        config_paths=[auth],
    ) == "env-secret"


def test_colocated_auth_yaml_is_compatible_fallback(tmp_path: Path) -> None:
    auth = tmp_path / "auth.yaml"
    auth.write_text("internal_verify_secret: file-secret\n", encoding="utf-8")
    assert load_control_plane_internal_auth_secret(
        environ={}, config_paths=[auth]
    ) == "file-secret"


def test_placeholder_secret_is_rejected(tmp_path: Path) -> None:
    auth = tmp_path / "auth.yaml"
    auth.write_text(
        "internal_verify_secret: change-me-internal-verify-secret\n",
        encoding="utf-8",
    )
    assert load_control_plane_internal_auth_secret(environ={}, config_paths=[auth]) == ""
