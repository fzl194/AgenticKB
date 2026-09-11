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
    # 快照-恢复而非清空：set_mining_service_config(None) 会把进程级缓存置空，
    # 同进程后续测试文件里任何 UploadConfig()/MiningConfig() 构造都会冷缓存
    # 懒拉控制面（本地 8910 拒连）——曾致全量套件 test_mining_auto_queue 5 挂。
    from knowledge_mining.mining.infra import control_plane as _cp
    saved = _cp._service_config_cache
    yield
    set_mining_service_config(saved)


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


def test_init_image_captioner_passes_full_async_config():
    """组合根全量透传（batch 审查问题五）：不只默认值。"""
    _set_config({
        "llm_service_url": "http://x:8900",
        "llm_call_mode": "async",
        "llm_async_poll_interval": 0.25,
        "llm_async_chat_wait_timeout": 77.0,
        "llm_async_max_attempts": 4,
    })
    from knowledge_mining.mining.jobs.run import _init_image_captioner

    captioner = _init_image_captioner("http://x:8900", knowledge_domain="d", enabled=True)
    assert captioner._call_mode == "async"
    assert captioner._task_client._poll_interval == 0.25
    assert captioner._task_client._wait_timeout == 77.0
    assert captioner._async_max_attempts == 4


def test_init_image_captioner_sync_mode_has_no_task_client():
    _set_config({"llm_service_url": "http://x:8900", "llm_call_mode": "sync"})
    from knowledge_mining.mining.jobs.run import _init_image_captioner

    captioner = _init_image_captioner("http://x:8900")
    assert captioner._call_mode == "sync"
    assert captioner._task_client is None


def test_close_llm_resources_closes_duck_typed_and_swallows_errors():
    from knowledge_mining.mining.jobs.run import _close_llm_resources

    closed = []

    class Closable:
        def close(self):
            closed.append("a")

    class Broken:
        def close(self):
            raise RuntimeError("boom")

    class Plain:
        pass  # 同步回退实现：没有 close()

    _close_llm_resources(Closable(), Broken(), Plain(), None)
    assert closed == ["a"]


def test_workflow_job_services_close_releases_llm_clients():
    """Run 终止释放（成功/失败/取消共用 finally 路径）。"""
    from knowledge_mining.mining.jobs.run import _WorkflowJobServices

    closed = []

    def _once(name):
        state = {"done": False}

        def close():
            if not state["done"]:  # 真实 LlmTaskClient.close 幂等
                state["done"] = True
                closed.append(name)

        return close

    class FakeGen:
        close = staticmethod(_once("embedding"))

    class FakeLlmGen:
        close = staticmethod(_once("generation"))

    class FakeCaptioner:
        close = staticmethod(_once("captioner"))

    svc = _WorkflowJobServices.__new__(_WorkflowJobServices)
    from types import SimpleNamespace
    svc.pipeline_config = SimpleNamespace(embedding_generator=FakeGen())
    svc._owned_llm_generator = FakeLlmGen()
    svc._llm_stage_services = {"image_captioner": FakeCaptioner(), "other": object()}

    svc.close()
    svc.close()  # 幂等
    assert closed == ["embedding", "generation", "captioner"]
