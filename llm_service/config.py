"""LLM Service configuration — single source: control plane YAML.

ALL config comes from main_control_service/config/system/llm_service.yaml.
No defaults, no env fallbacks. Missing required fields = hard error.

Bootstrap parameters are CONTROL_PLANE_BASE_URL and the optional
CONTROL_PLANE_INTERNAL_AUTH_SECRET. In the all-in-one container the latter
falls back to main_control's colocated auth.yaml.

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

from main_control_service.internal_auth import (
    control_plane_internal_headers,
    load_control_plane_internal_auth_secret,
)

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


def fetch_internal_verify_secret() -> str:
    """Load the bootstrap internal secret from env or colocated auth.yaml.

    The control-plane proxy strips client-forged X-Internal-Auth headers and
    injects the real secret only for authenticated users — destructive admin
    endpoints use it (plus X-KB-Role) as a fail-closed gate. Returns "" on
    any failure so those endpoints refuse to run rather than open up.
    """
    secret = load_control_plane_internal_auth_secret()
    if not secret:
        logger.warning(
            "Could not load internal_verify_secret from env or colocated auth.yaml — "
            "destructive admin endpoints will fail closed"
        )
    return secret


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
        resp = httpx.get(
            endpoint,
            headers=control_plane_internal_headers(),
            timeout=timeout,
            proxy=None,
            trust_env=False,
        )
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


# ---------------------------------------------------------------------------
# Per-domain configs (62号：chat/rerank/embedding 按域覆盖，embedding 维度锁全局)
# ---------------------------------------------------------------------------

#: 全局配置在 DomainConfigs 中的键（无覆盖域与 None 域共用全局配置）。
GLOBAL_DOMAIN_KEY = "__global__"


def embedding_dims_locked(domain_data: dict, global_cfg: dict) -> bool:
    """域 effective 的 embedding.dimensions 必须与全局一致（62号二期）。

    模型/端点/密钥可按域覆盖；维度锁全局——检索表向量列定长（vector(N)），
    所有域共享，维度漂移会在入库/检索两侧同时炸。严格相等、fail-closed：
    域 effective 缺维度而全局有（控制面 deep-merge 后正常不会发生）也判漂移。
    """
    d_dim = (domain_data.get("embedding") or {}).get("dimensions")
    g_dim = (global_cfg.get("embedding") or {}).get("dimensions")
    return d_dim == g_dim


class DomainConfigs:
    """全局基底 + 各域 effective 配置的持有者。

    merge 在控制面完成（``GET /api/v1/system/llm_service/effective/{domain}``），
    这里只持有成品。``reload``/``replace_with`` 原子换内部引用——LLMService、
    ModelService、Worker 都经 ``for_domain`` 每次取当前生效配置，热加载无需
    重建组件。坏覆盖在装载时即作废（``invalid``），域回落全局，服务不崩。
    """

    def __init__(
        self,
        global_cfg: dict[str, Any],
        domain_cfgs: dict[str, dict[str, Any]] | None = None,
        invalid: dict[str, str] | None = None,
    ) -> None:
        self._global = global_cfg
        self._domains: dict[str, dict[str, Any]] = dict(domain_cfgs or {})
        self._invalid: dict[str, str] = dict(invalid or {})

    @classmethod
    def single(cls, global_cfg: dict[str, Any]) -> "DomainConfigs":
        """无任何域覆盖的退化形态（等价于现状单配置）。"""
        return cls(global_cfg)

    @property
    def global_config(self) -> dict[str, Any]:
        return self._global

    def for_domain(self, domain: str | None) -> dict[str, Any]:
        """该域的 effective 配置；无覆盖/未知域/None → 全局配置。"""
        if domain and domain in self._domains:
            return self._domains[domain]
        return self._global

    def has_override(self, domain: str | None) -> bool:
        return bool(domain) and domain in self._domains

    def domain_key(self, domain: str | None) -> str:
        """provider 缓存键的域段：有覆盖用域 id，否则全局键。"""
        return domain if self.has_override(domain) else GLOBAL_DOMAIN_KEY

    def reload(
        self,
        global_cfg: dict[str, Any],
        domain_cfgs: dict[str, dict[str, Any]] | None = None,
        invalid: dict[str, str] | None = None,
    ) -> None:
        self._global = global_cfg
        self._domains = dict(domain_cfgs or {})
        self._invalid = dict(invalid or {})

    def replace_with(self, other: "DomainConfigs") -> None:
        """以另一实例的内容原子替换自身（保住所有持有者的引用）。"""
        self.reload(other._global, other._domains, other._invalid)

    def status(self) -> dict[str, Any]:
        """运维可见摘要（不含密钥）——/admin/config-status 消费。"""
        def _active(cfg: dict[str, Any]) -> Any:
            return (cfg.get("provider") or {}).get("active_model") or (cfg.get("provider") or {}).get("model")

        def _emb(cfg: dict[str, Any]) -> dict[str, Any]:
            emb = cfg.get("embedding") or {}
            return {
                "embedding_model": emb.get("model"),
                "embedding_dimensions": emb.get("dimensions"),
            }

        def _vision(cfg: dict[str, Any]) -> Any:
            return (cfg.get("provider") or {}).get("vision_model")

        return {
            "global": {
                "active_model": _active(self._global),
                "vision_model": _vision(self._global),
                **_emb(self._global),
            },
            "domains": {
                did: {
                    "active_model": _active(cfg),
                    "vision_model": _vision(cfg),
                    "rerank_model": (cfg.get("rerank") or {}).get("model"),
                    **_emb(cfg),
                }
                for did, cfg in self._domains.items()
            },
            "invalid_domains": dict(self._invalid),
        }


def fetch_llm_domain_overrides(
    base_url: str | None = None,
    timeout: float = 5.0,
    global_cfg: dict[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """拉取各域 effective 配置。单个域失败只作废该域（回落全局），绝不致命。

    返回 ``(domain_cfgs, invalid)``。清单接口不可达时返回两个空 dict——
    兼容控制面未升级到 62号 的部署（llm_service 先到、控制面后到）。
    ``global_cfg`` 提供时复验各域 embedding 维度与全局一致（62号二期：
    模型/端点/密钥按域可变、维度锁全局——控制面校验之外的兜底闸）。
    """
    url = (base_url or CONTROL_PLANE_BASE_URL).rstrip("/")
    try:
        resp = httpx.get(
            f"{url}/api/v1/system/llm_service/domains",
            headers=control_plane_internal_headers(),
            timeout=timeout,
            proxy=None,
            trust_env=False,
        )
        resp.raise_for_status()
        items = (resp.json() or {}).get("items") or []
    except Exception as exc:
        logger.error(
            "Cannot list llm domain overrides from control plane (%s): %s — "
            "falling back to global-only config", url, exc,
        )
        return {}, {}

    domain_cfgs: dict[str, dict[str, Any]] = {}
    invalid: dict[str, str] = {}
    for entry in items:
        did = str((entry or {}).get("domain_id") or "")
        if not did:
            continue
        try:
            r = httpx.get(
                f"{url}/api/v1/system/llm_service/effective/{did}",
                headers=control_plane_internal_headers(),
                timeout=timeout,
                proxy=None,
                trust_env=False,
            )
            r.raise_for_status()
            data = yaml.safe_load(r.text)
            if not isinstance(data, dict):
                raise ValueError(f"non-dict YAML for domain {did!r}")
            data = _resolve_env_vars(data)
            _validate_required(data)
            if global_cfg is not None and not embedding_dims_locked(data, global_cfg):
                raise ValueError(
                    f"embedding dimensions {dig_optional(data, 'embedding', 'dimensions')} != global "
                    f"{dig_optional(global_cfg, 'embedding', 'dimensions')} — per-domain embedding "
                    "model override is allowed but dimensions are locked to global "
                    "(shared fixed-width vector column, 62号 phase 2)"
                )
            domain_cfgs[did] = data
        except Exception as exc:
            invalid[did] = str(exc)
            logger.error(
                "llm domain override for %s discarded (domain falls back to global): %s",
                did, exc,
            )
    return domain_cfgs, invalid


def load_domain_configs(base_url: str | None = None) -> DomainConfigs:
    """全局配置 + 各域覆盖一次拉齐。全局失败仍然致命（与现状一致）。"""
    global_cfg = fetch_config_from_control_plane(base_url)
    domain_cfgs, invalid = fetch_llm_domain_overrides(base_url, global_cfg=global_cfg)
    dc = DomainConfigs(global_cfg, domain_cfgs, invalid)
    logger.info(
        "Loaded config from control plane: global + %d domain override(s)%s",
        len(domain_cfgs),
        f", {len(invalid)} discarded" if invalid else "",
    )
    return dc
