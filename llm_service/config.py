"""LLM Service configuration — single source: control plane YAML.

ALL config comes from main_control_service/config/system/llm_service.yaml.
No defaults, no env fallbacks. Missing required fields = hard error.

Exception: CONTROL_PLANE_BASE_URL is the single bootstrap parameter that
tells this service where to find its config. It may be set via env var.

Secrets (api_key fields) support ``${ENV_VAR}`` syntax — resolved at load time
from the process environment.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

# Pattern: ${VAR} or ${VAR:-default}
_ENV_RE = re.compile(r"\$\{([^}]+)\}")

# Bootstrap: the one URL needed before we can fetch everything else.
CONTROL_PLANE_BASE_URL = os.getenv("CONTROL_PLANE_BASE_URL", "http://localhost:8910")

# ── All required field paths — missing = fatal ──
_REQUIRED_PATHS: list[tuple[str, ...]] = [
    ("host",),
    ("port",),
    ("provider", "active_model"),
    ("provider", "timeout"),
    ("provider", "bypass_proxy"),
    ("provider", "headers"),
    ("embedding", "base_url"),
    ("embedding", "api_key"),
    ("embedding", "model"),
    ("embedding", "timeout"),
    ("embedding", "bypass_proxy"),
    ("embedding", "headers"),
    ("rerank", "base_url"),
    ("rerank", "api_key"),
    ("rerank", "model"),
    ("rerank", "timeout"),
    ("rerank", "bypass_proxy"),
    ("rerank", "headers"),
    ("worker", "concurrency"),
    ("worker", "poll_interval"),
    ("task", "default_max_attempts"),
    ("task", "retry_backoff_base"),
    ("task", "retry_backoff_max"),
    ("task", "lease_duration"),
    ("task", "lease_recovery_interval"),
    ("template", "cache_ttl"),
]


def dig(data: dict, *keys: str) -> Any:
    """Walk nested dict by *keys*; raise ValueError on miss."""
    node: Any = data
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            raise ValueError(f"Missing config field: {'.'.join(keys)}")
        node = node[k]
    return node


def _expand_env(value: str) -> str:
    """Replace ``${VAR}`` and ``${VAR:-default}`` patterns with env values."""
    def _replace(m: re.Match) -> str:
        expr = m.group(1)
        if ":-" in expr:
            var, default = expr.split(":-", 1)
            return os.getenv(var.strip(), default)
        return os.getenv(expr.strip(), "")
    return _ENV_RE.sub(_replace, value)


def _resolve_env_vars(data: Any) -> Any:
    """Recursively resolve ``${VAR}`` in all string values."""
    if isinstance(data, dict):
        return {k: _resolve_env_vars(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_env_vars(v) for v in data]
    if isinstance(data, str) and "${" in data:
        return _expand_env(data)
    return data


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep-merge *override* into *base* — nested dicts are merged, not replaced."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def dig_optional(data: dict, *keys: str, default: Any = None) -> Any:
    """Walk nested dict by *keys*; return *default* on miss."""
    node: Any = data
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


def resolve_model_config(cfg: dict, model_key: str | None = None) -> dict:
    """Resolve provider config for ``model_key`` (or active_model when None).

    ``model_key`` may be a key under ``provider.models`` (e.g. ``glm-4.5v``)
    or the underlying ``model`` field value (e.g. ``glm-4.5v`` API id).
    """
    provider_cfg = cfg.get("provider", {})
    models = provider_cfg.get("models") or {}
    active = model_key or provider_cfg.get("active_model")

    if not models or not active:
        return dict(provider_cfg)

    resolved_key = active
    if resolved_key not in models:
        for key, entry in models.items():
            if isinstance(entry, dict) and entry.get("model") == active:
                resolved_key = key
                break
        else:
            if model_key is not None:
                raise ValueError(
                    f"Unknown chat model {model_key!r}. "
                    f"Known keys: {sorted(models)}"
                )
            logger.warning(
                "active_model '%s' not found in provider.models, using base config",
                active,
            )
            return dict(provider_cfg)

    return _deep_merge(provider_cfg, models[resolved_key])


def resolve_active_model_config(cfg: dict) -> dict:
    """Resolve the effective provider config considering multi-model setup.

    If ``provider.models`` exists and ``provider.active_model`` is set,
    merge the active model's overrides into the base provider config.
    Otherwise return the provider config as-is (single-model mode).
    """
    return resolve_model_config(cfg, model_key=None)


def _validate_required(data: dict) -> None:
    """Raise ValueError if any required field is missing or empty string."""
    missing: list[str] = []
    for path in _REQUIRED_PATHS:
        try:
            val = data
            for k in path:
                val = val[k]
            if isinstance(val, str) and not val.strip():
                missing.append(".".join(path))
        except (KeyError, TypeError):
            missing.append(".".join(path))
    if missing:
        raise ValueError(
            f"Missing required config fields: {', '.join(missing)}. "
            f"Update llm_service.yaml in the control plane."
        )

    # Validate that the resolved active model has base_url and api_key
    resolved = resolve_active_model_config(data)
    if not resolved.get("base_url"):
        raise ValueError("Resolved provider config missing 'base_url'. Check provider.models entry for active_model.")
    if not resolved.get("api_key"):
        raise ValueError("Resolved provider config missing 'api_key'. Check provider.models entry for active_model.")
    if not resolved.get("model"):
        raise ValueError("Resolved provider config missing 'model'. Check provider.models entry for active_model.")


def fetch_config_from_control_plane(
    base_url: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Fetch and validate llm_service.yaml from control plane. Fatal on failure."""
    url = (base_url or CONTROL_PLANE_BASE_URL).rstrip("/")
    endpoint = f"{url}/api/v1/system/llm_service/raw"
    try:
        resp = httpx.get(endpoint, timeout=timeout, proxy=None, trust_env=False)
        resp.raise_for_status()
        data = yaml.safe_load(resp.text)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot fetch config from control plane ({endpoint}): {exc}. "
            f"Ensure main_control_service is running and llm_service.yaml exists."
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(f"Control plane returned non-dict YAML for llm_service")

    data = _resolve_env_vars(data)
    _validate_required(data)
    logger.info("Loaded config from control plane (%s)", endpoint)
    return data


def load_llm_config() -> dict[str, Any]:
    """Load config from control plane. Raises on any failure."""
    return fetch_config_from_control_plane()
