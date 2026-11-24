"""默认视觉模型（vision_model）解析测试。

模型选择归 llm_service 管：挖掘侧图注算子不再硬编码模型名，而是通过
GET /api/v1/models/vision-default 拿到本环境配置的默认视觉模型。

覆盖：
- ProviderResolver.vision_default_model：全局/域覆盖 vision_model、未配置返回 None
- GET /api/v1/models/vision-default：vision_model → active_model → null 回落
- DomainConfigs.status：摘要含 vision_model（不含密钥）
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_service.config import DomainConfigs
from llm_service.runtime.provider_resolver import ProviderResolver
from llm_service.tests.conftest import TEST_CFG

pytestmark = pytest.mark.asyncio


def _cfg(vision: str | None = None, active: str = "g-active") -> dict:
    provider: dict = {
        "active_model": active,
        "timeout": 30,
        "bypass_proxy": False,
        "headers": {},
        "models": {
            "g-active": {"model": "g-active", "base_url": "u1", "api_key": "gk1"},
            "g-vlm": {"model": "g-vlm-id", "base_url": "u1", "api_key": "gk1"},
        },
    }
    if vision is not None:
        provider["vision_model"] = vision
    return {**TEST_CFG, "provider": provider}


# ---------------------------------------------------------------------------
# ProviderResolver.vision_default_model
# ---------------------------------------------------------------------------

async def test_resolver_vision_default_reads_global_config():
    resolver = ProviderResolver(DomainConfigs.single(_cfg(vision="g-vlm")))
    assert resolver.vision_default_model() == "g-vlm"


async def test_resolver_vision_default_unset_returns_none():
    resolver = ProviderResolver(DomainConfigs.single(_cfg()))
    assert resolver.vision_default_model() is None


async def test_resolver_vision_default_domain_override():
    domain = _cfg()
    domain["provider"] = {**domain["provider"], "vision_model": "odn-vlm"}
    resolver = ProviderResolver(DomainConfigs(_cfg(vision="g-vlm"), {"odn": domain}))
    assert resolver.vision_default_model(domain="odn") == "odn-vlm"
    assert resolver.vision_default_model() == "g-vlm"


# ---------------------------------------------------------------------------
# GET /api/v1/models/vision-default
# ---------------------------------------------------------------------------

def _request(resolver: ProviderResolver | None) -> SimpleNamespace:
    state = SimpleNamespace()
    if resolver is not None:
        state.provider_resolver = resolver
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def test_vision_default_endpoint_prefers_vision_model():
    from llm_service.api import model_api

    resolver = ProviderResolver(DomainConfigs.single(_cfg(vision="g-vlm")))
    out = await model_api.vision_default(_request(resolver))
    assert out == {"success": True, "data": {"model": "g-vlm", "source": "vision_model"}}


async def test_vision_default_endpoint_falls_back_to_active_model():
    from llm_service.api import model_api

    resolver = ProviderResolver(DomainConfigs.single(_cfg()))
    out = await model_api.vision_default(_request(resolver))
    assert out == {"success": True, "data": {"model": "g-active", "source": "active_model"}}


async def test_vision_default_endpoint_no_model_configured():
    from llm_service.api import model_api

    out = await model_api.vision_default(_request(None))
    assert out == {"success": True, "data": {"model": None, "source": "none"}}


# ---------------------------------------------------------------------------
# status 摘要
# ---------------------------------------------------------------------------

async def test_status_summary_includes_vision_model():
    dc = DomainConfigs.single(_cfg(vision="g-vlm"))
    assert dc.status()["global"]["vision_model"] == "g-vlm"
    dc2 = DomainConfigs.single(_cfg())
    assert dc2.status()["global"]["vision_model"] is None
