"""Bootstrap credentials for trusted control-plane configuration readers."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Mapping

import yaml

SECRET_ENV = "CONTROL_PLANE_INTERNAL_AUTH_SECRET"
AUTH_CONFIG_PATH_ENV = "CONTROL_PLANE_AUTH_CONFIG_PATH"


def _valid_secret(value: object) -> str:
    secret = value.strip() if isinstance(value, str) else ""
    return "" if not secret or secret.startswith("change-me") else secret


def _default_auth_paths(environ: Mapping[str, str]) -> tuple[Path, ...]:
    configured = environ.get(AUTH_CONFIG_PATH_ENV, "").strip()
    paths = ([Path(configured)] if configured else []) + [
        Path(__file__).resolve().parent / "config" / "system" / "auth.yaml",
        Path("main_control_service/config/system/auth.yaml"),
        Path("../main_control_service/config/system/auth.yaml"),
    ]
    return tuple(dict.fromkeys(paths))


def load_control_plane_internal_auth_secret(
    *,
    environ: Mapping[str, str] | None = None,
    config_paths: Iterable[Path] | None = None,
) -> str:
    """Return env secret first, then the colocated main_control auth.yaml secret.

    An empty result is intentional: callers send no credential and main_control
    fails closed instead of treating network location as authentication.
    """
    env = os.environ if environ is None else environ
    from_env = _valid_secret(env.get(SECRET_ENV))
    if from_env:
        return from_env
    for path in config_paths or _default_auth_paths(env):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        from_file = _valid_secret(data.get("internal_verify_secret"))
        if from_file:
            return from_file
    return ""


def control_plane_internal_headers() -> dict[str, str]:
    secret = load_control_plane_internal_auth_secret()
    return {"X-Internal-Auth": secret} if secret else {}
