"""MiningConfig 新字段与组合根模式选择（_init_embedding）单测。

锁定：默认 async（内网零配置变更即生效）、显式 sync 回退、
llm_base_url 为空时 None（embedding_handler FAILED embedding_unavailable 不变）。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.infra.control_plane import set_mining_service_config
from knowledge_mining.mining.infra.embedding import (
    LLMServiceAsyncEmbeddingGenerator,
    LLMServiceEmbeddingGenerator,
)
from knowledge_mining.mining.infra.mining_config import MiningConfig
from knowledge_mining.mining.jobs.run import _init_embedding


@pytest.fixture(autouse=True)
def _reset_control_plane():
    yield
    set_mining_service_config(None)


def _set_config(payload: dict | None) -> None:
    set_mining_service_config(payload)


def test_mining_config_defaults_to_async():
    # 控制面未下发新键（存量内网 mining.yaml 的真实形态）→ 代码默认 async
    _set_config({"llm_service_url": "http://x:8900"})
    cfg = MiningConfig()
    assert cfg.llm_call_mode == "async"
    assert cfg.llm_async_poll_interval == 1.0
    assert cfg.llm_async_wait_timeout == 900.0
    assert cfg.llm_async_chat_wait_timeout == 300.0
    assert cfg.llm_async_max_attempts == 3


def test_mining_config_explicit_sync_and_overrides():
    _set_config({
        "llm_service_url": "http://x:8900",
        "llm_call_mode": "sync",
        "llm_async_wait_timeout": 60,
    })
    cfg = MiningConfig()
    assert cfg.llm_call_mode == "sync"
    assert cfg.llm_async_wait_timeout == 60.0
    assert cfg.llm_async_max_attempts == 3  # 未覆盖的键保持默认


def test_init_embedding_selects_async_by_default():
    _set_config({"llm_service_url": "http://x:8900"})  # 不含新键
    gen = _init_embedding("http://x:8900")
    assert isinstance(gen, LLMServiceAsyncEmbeddingGenerator)


def test_init_embedding_sync_mode_returns_sync_generator():
    _set_config({"llm_service_url": "http://x:8900", "llm_call_mode": "sync"})
    gen = _init_embedding("http://x:8900")
    assert isinstance(gen, LLMServiceEmbeddingGenerator)


def test_init_embedding_explicit_mode_param_beats_config():
    _set_config({"llm_service_url": "http://x:8900", "llm_call_mode": "async"})
    gen = _init_embedding("http://x:8900", mode="sync")
    assert isinstance(gen, LLMServiceEmbeddingGenerator)


def test_init_embedding_blank_url_returns_none():
    assert _init_embedding(None) is None
    assert _init_embedding("") is None
