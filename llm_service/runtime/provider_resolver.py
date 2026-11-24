"""(domain, model) → provider 的统一解析与缓存（62号第一期）。

LLMService / ModelService / Worker 三处共用一个实例，保证同一次请求在
提交侧与执行侧解析到同一个 provider。缓存键 = (域键, 模型键)；域键在有
覆盖时是域 id、否则是全局键——因此无覆盖域天然与全局共享缓存条目，
无覆盖部署的行为与单配置时代逐字节一致。

热加载（/admin/reload-config）只 clear_cache() + 原子换 DomainConfigs：
在途任务已把 model 落库，重建 provider 按新配置解析，互不干扰。
"""
from __future__ import annotations

import logging

from llm_service.config import (
    DomainConfigs,
    GLOBAL_DOMAIN_KEY,
    dig_optional,
    resolve_active_model_config,
    resolve_model_config,
)
from llm_service.providers.base import ProviderProtocol
from llm_service.providers.bigmodel_models import BigModelProvider
from llm_service.providers.factory import build_chat_provider
from llm_service.providers.model_base import ModelProviderProtocol

logger = logging.getLogger(__name__)


def build_model_provider(cfg: dict) -> BigModelProvider:
    """从（effective）配置构建 embedding+rerank 的 BigModelProvider。

    embedding 与 rerank 半边都取自传入的（域）effective 配置（62号二期起
    embedding 模型/端点/密钥可按域覆盖；维度由装载闸保证与全局一致）。
    """
    return BigModelProvider(
        embedding_api_key=dig_optional(cfg, "embedding", "api_key"),
        embedding_url=dig_optional(cfg, "embedding", "base_url"),
        embedding_model=dig_optional(cfg, "embedding", "model"),
        embedding_timeout=dig_optional(cfg, "embedding", "timeout"),
        embedding_bypass_proxy=dig_optional(cfg, "embedding", "bypass_proxy"),
        embedding_headers=dig_optional(cfg, "embedding", "headers") or {},
        # BUG-01：不接受 dimensions 参数的 embedding 网关按域关闭（默认开）。
        embedding_send_dimensions=bool(
            dig_optional(cfg, "embedding", "send_dimensions", default=True)
        ),
        rerank_api_key=dig_optional(cfg, "rerank", "api_key"),
        rerank_url=dig_optional(cfg, "rerank", "base_url"),
        rerank_model=dig_optional(cfg, "rerank", "model"),
        rerank_timeout=dig_optional(cfg, "rerank", "timeout"),
        rerank_bypass_proxy=dig_optional(cfg, "rerank", "bypass_proxy"),
        rerank_headers=dig_optional(cfg, "rerank", "headers") or {},
    )


class ProviderResolver:
    """Chat/embedding+rerank provider 的按域懒构建缓存。"""

    def __init__(self, domain_configs: DomainConfigs) -> None:
        self._dc = domain_configs
        # (域键, 模型键) → chat provider；模型键 None = 该域 active_model
        self._chat: dict[tuple[str, str | None], ProviderProtocol] = {}
        # 域键 → BigModelProvider（embedding+rerank）
        self._model: dict[str, ModelProviderProtocol] = {}

    # ------------------------------------------------------------------
    # Seeding — startup providers become the global cache entries
    # ------------------------------------------------------------------

    def seed_chat(self, provider: ProviderProtocol) -> None:
        """把启动期构建的全局 active provider 播种为全局缓存条目。

        幂等：已播种则跳过。种子在 reload 清缓存后由新配置懒重建替代。
        """
        if (GLOBAL_DOMAIN_KEY, None) in self._chat:
            return
        self._chat[(GLOBAL_DOMAIN_KEY, None)] = provider
        active = (self._dc.for_domain(None).get("provider") or {}).get("active_model")
        if active:
            self._chat[(GLOBAL_DOMAIN_KEY, active)] = provider

    def seed_model_provider(self, provider: ModelProviderProtocol) -> None:
        self._model.setdefault(GLOBAL_DOMAIN_KEY, provider)

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def chat_provider(
        self, model: str | None = None, domain: str | None = None,
    ) -> ProviderProtocol:
        """解析 (domain, model) 的 chat provider。

        model=None → 该域 effective 配置的 active_model（无覆盖域命中
        播种的全局启动 provider）。显式 model → 在该域 effective models
        里解析，未知时 ValueError（与单配置时代一致）。
        """
        cfg = self._dc.for_domain(domain)
        dkey = self._dc.domain_key(domain)

        if model is None:
            cached = self._chat.get((dkey, None))
            if cached is not None:
                return cached
            provider, _ = build_chat_provider(resolve_active_model_config(cfg))
            self._chat[(dkey, None)] = provider
            active = (cfg.get("provider") or {}).get("active_model")
            if active:
                self._chat[(dkey, active)] = provider
            return provider

        # Normalize the caller-supplied key: provider.models entry name or API model id.
        models = (cfg.get("provider") or {}).get("models") or {}
        key = model
        if key not in models:
            for k, entry in models.items():
                if isinstance(entry, dict) and entry.get("model") == model:
                    key = k
                    break
        cached = self._chat.get((dkey, key))
        if cached is not None:
            return cached
        # Raises ValueError for unknown explicit model (single-config-era behavior).
        provider, _ = build_chat_provider(resolve_model_config(cfg, model_key=model))
        self._chat[(dkey, key)] = provider
        return provider

    # ------------------------------------------------------------------
    # Embedding + rerank
    # ------------------------------------------------------------------

    def model_provider(self, domain: str | None = None) -> ModelProviderProtocol:
        """该域的 BigModelProvider（无覆盖域 = 全局实例，通常是播种的启动实例）。"""
        dkey = self._dc.domain_key(domain)
        cached = self._model.get(dkey)
        if cached is not None:
            return cached
        provider = build_model_provider(self._dc.for_domain(domain))
        self._model[dkey] = provider
        return provider

    def rerank_default_model(self, domain: str | None = None) -> str:
        """该域的 rerank 默认模型（effective 配置已含全局兜底）。"""
        cfg = self._dc.for_domain(domain)
        return str(dig_optional(cfg, "rerank", "model", default="") or "")

    def embedding_default_model(self, domain: str | None = None) -> str:
        """该域的 embedding 默认模型（62号二期；effective 已含全局兜底）。"""
        cfg = self._dc.for_domain(domain)
        return str(dig_optional(cfg, "embedding", "model", default="") or "")

    def embedding_default_dimensions(self, domain: str | None = None) -> int | None:
        """该域的 embedding 维度（锁全局；缺省返回 None）。"""
        cfg = self._dc.for_domain(domain)
        dims = dig_optional(cfg, "embedding", "dimensions")
        return int(dims) if dims is not None else None

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """清空缓存（reload 后按新配置懒重建；旧 provider 由 GC 回收）。"""
        self._chat.clear()
        self._model.clear()
