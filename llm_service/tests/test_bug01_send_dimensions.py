"""BUG-01（P1）回归：embedding dimensions 死锁——加参数 0 向量、删参数提交 500。

两处修复的钉测：
1. service.submit_embedding：配置缺 embedding.dimensions 时不再 ValueError
   （改用 dig_optional，与同步端点 model_service 对齐），None 直通。
2. BigModelProvider.embed：embedding.send_dimensions=false 的网关
   （MaaS bge-m3，Pydantic extra_forbidden——payload 带 dimensions 即
   HTTP 200 + 空 data → 0 向量死信）绝不把 dimensions 写进 payload；
   默认 True 保持智谱 embedding-3 行为（需 dimensions 压 1024 维）。
"""
from __future__ import annotations

import json

import pytest

from llm_service.providers.bigmodel_models import BigModelProvider
from llm_service.runtime.service import LLMService
from llm_service.tests.conftest import TEST_CFG

pytestmark = pytest.mark.asyncio


# ── 修复 1：配置缺失 embedding.dimensions 不再 500 ─────────────────────


class _FakeConn:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []

    async def execute(self, sql, params):
        self.executes.append((sql, params))

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self):
        self.conn = _FakeConn()

    def connection(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _FakeDB:
    def __init__(self):
        self.pool = _FakePool()

    async def fetchone(self, sql, params):
        return None

    async def execute(self, sql, params):
        pass


async def test_submit_without_dimensions_config_does_not_raise():
    """配置删掉 embedding.dimensions 后提交不再 500（BUG-01 崩溃分支）。"""
    db = _FakeDB()
    cfg = {**TEST_CFG, "embedding": {"model": "m1"}}  # 无 dimensions 键
    svc = LLMService(db=db, provider=None, config=cfg, templates=object())
    await svc.submit_embedding(["a"], idempotency_key="bug01-1")
    for sql, params in db.pool.conn.executes:
        if "INSERT INTO agent_llm_requests" in sql:
            assert json.loads(params[6])["dimensions"] is None
            return
    raise AssertionError("agent_llm_requests 未插入")


async def test_submit_with_dimensions_config_still_resolved():
    """保留 embedding.dimensions: 1024 时提交侧解析不变（不回归 62 号二期）。"""
    db = _FakeDB()
    cfg = {**TEST_CFG, "embedding": {"model": "m1", "dimensions": 1024}}
    svc = LLMService(db=db, provider=None, config=cfg, templates=object())
    await svc.submit_embedding(["a"], idempotency_key="bug01-2")
    for sql, params in db.pool.conn.executes:
        if "INSERT INTO agent_llm_requests" in sql:
            assert json.loads(params[6])["dimensions"] == 1024
            return
    raise AssertionError("agent_llm_requests 未插入")


# ── 修复 2：send_dimensions 开关控制 payload ────────────────────────────


class _CaptureProvider(BigModelProvider):
    """截获 _post 的 payload，不发出网络请求。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured: list[dict] = []

    async def _post(self, url, api_key, capability, payload):
        self.captured.append(payload)
        return {"data": [{"index": i, "embedding": [0.1] * 4} for i in range(len(payload["input"]))]}


async def test_send_dimensions_false_omits_payload_field():
    p = _CaptureProvider(embedding_send_dimensions=False)
    await p.embed(["a", "b"], dimensions=1024)
    assert len(p.captured) == 1
    assert "dimensions" not in p.captured[0], "bge-m3 网关（extra_forbidden）绝不接受该字段"


async def test_send_dimensions_default_true_keeps_payload_field():
    p = _CaptureProvider()
    await p.embed(["a"], dimensions=1024)
    assert p.captured[0].get("dimensions") == 1024, "智谱 embedding-3 依赖该字段压到 1024 维"


async def test_send_dimensions_false_without_dimensions_also_clean():
    p = _CaptureProvider(embedding_send_dimensions=False)
    await p.embed(["a"], dimensions=None)
    assert "dimensions" not in p.captured[0]
