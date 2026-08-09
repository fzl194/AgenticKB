"""Build chat Provider instances from resolved provider config dicts."""
from __future__ import annotations

from typing import Any


def build_chat_provider(active_provider: dict[str, Any]) -> tuple[Any, str]:
    """Build a chat Provider from a resolved provider config.

    Returns ``(provider, provider_type)``.
    """
    from llm_service.providers.anthropic import AnthropicProvider
    from llm_service.providers.openai_compatible import OpenAICompatibleProvider

    provider_type = active_provider.get("provider_type", "openai_compatible")
    model = active_provider.get("model", active_provider.get("active_model", ""))

    if provider_type == "anthropic":
        provider = AnthropicProvider(
            api_key=active_provider["api_key"],
            model=model,
            base_url=active_provider.get(
                "base_url", "https://api.anthropic.com/v1/messages"
            ),
            api_version=active_provider.get("api_version", "2023-06-01"),
            headers=active_provider.get("headers") or {},
            timeout=active_provider.get("timeout", 60),
            bypass_proxy=active_provider.get("bypass_proxy", False),
        )
    else:
        provider = OpenAICompatibleProvider(
            base_url=active_provider["base_url"],
            api_key=active_provider["api_key"],
            model=model,
            headers=active_provider.get("headers") or {},
            timeout=active_provider.get("timeout", 30),
            bypass_proxy=active_provider.get("bypass_proxy", False),
        )
    return provider, provider_type
