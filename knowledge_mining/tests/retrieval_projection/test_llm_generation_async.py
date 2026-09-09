"""LLMServiceAsyncGenerationClient 单测（MockTransport 假 llm_service）。

锁定：结果映射与同步 /execute 客户端逐分支对齐（json/text/正则兜底/空文
本/dead_letter/超时），以及两个适配器在异步客户端下的失败语义不变
（question→LLM_FAILURE 哨兵、summary→异常上抛）。
"""
from __future__ import annotations

import json

import httpx
import pytest

from knowledge_mining.mining.retrieval_projection.llm_generation import (
    LLM_FAILURE,
    LLMQuestionGenerator,
    LLMServiceAsyncGenerationClient,
    LLMSummarizer,
)


class _FakeLlm:
    """模拟任务通道：提交 → batch-status 返回设定终态与结果。"""

    def __init__(self, *, status: str = "succeeded", result: dict | None = None):
        self.submits: list[dict] = []
        self._status = status
        self._result = result or {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.read())
        if path == "/api/v1/tasks":
            self.submits.append(body)
            return httpx.Response(200, json={
                "success": True,
                "data": {"task_id": "task-1", "status": "queued"},
            })
        if path == "/api/v1/tasks/batch-status":
            entry: dict = {"task_id": "task-1", "status": self._status}
            if self._status == "succeeded":
                entry["result"] = self._result
            elif self._status in ("failed", "dead_letter"):
                entry["error"] = {"error_type": "rate_limited", "error_message": "429"}
            return httpx.Response(200, json={
                "success": True, "data": {"tasks": [entry], "not_found": []},
            })
        raise AssertionError(f"unexpected path {path}")

    def client(self, **kwargs) -> LLMServiceAsyncGenerationClient:
        return LLMServiceAsyncGenerationClient(
            base_url="http://fake",
            poll_interval=0.01,
            wait_timeout=0.5,
            transport=httpx.MockTransport(self.handler),
            **kwargs,
        )


def _result(parsed=None, text=None, parse_status="succeeded"):
    return {"parse_status": parse_status, "parsed_output": parsed, "text_output": text}


def test_execute_returns_parsed_json_object():
    fake = _FakeLlm(result=_result(parsed={"question": "q", "answer_span": "a"}))
    out = fake.client().execute(
        [{"role": "user", "content": "x"}], expected_output_type="json_object",
    )
    assert out == {"question": "q", "answer_span": "a"}
    # 提交形状：messages 直传 + 幂等键 + 默认 3 次重试
    assert fake.submits[0]["messages"] == [{"role": "user", "content": "x"}]
    assert fake.submits[0]["expected_output_type"] == "json_object"
    assert fake.submits[0]["max_attempts"] == 3


def test_execute_json_falls_back_to_regex_on_missing_parsed():
    fake = _FakeLlm(result=_result(parsed=None, text='前缀 {"ok": true} 后缀'))
    out = fake.client().execute(
        [{"role": "user", "content": "x"}], expected_output_type="json_object",
    )
    assert out == {"ok": True}


def test_execute_json_parse_failure_raises():
    fake = _FakeLlm(result=_result(parsed=None, text="no json here"))
    with pytest.raises(RuntimeError, match="json parse failed"):
        fake.client().execute(
            [{"role": "user", "content": "x"}], expected_output_type="json_object",
        )


def test_execute_returns_text_output():
    fake = _FakeLlm(result=_result(text="  摘要正文  "))
    out = fake.client().execute([{"role": "user", "content": "x"}])
    assert out == "  摘要正文  "


def test_execute_raises_on_empty_text():
    fake = _FakeLlm(result=_result(text="   "))
    with pytest.raises(RuntimeError, match="empty output"):
        fake.client().execute([{"role": "user", "content": "x"}])


@pytest.mark.parametrize("status", ["failed", "dead_letter", "timeout"])
def test_execute_raises_on_dead_letter_with_task_id(status):
    fake = _FakeLlm(status=status)
    with pytest.raises(RuntimeError, match="task-1"):
        fake.client().execute([{"role": "user", "content": "x"}])


def test_question_generator_failure_yields_llm_failure_sentinel():
    """语义锁定：异步客户端抛错 → 逐项 LLM_FAILURE（facade degraded）。"""
    fake = _FakeLlm(status="dead_letter")
    gen = LLMQuestionGenerator(fake.client(), max_items=2)
    out = gen.generate_questions([{"text": "一些内容"}])
    assert out == [LLM_FAILURE]


def test_question_generator_success_path():
    fake = _FakeLlm(result=_result(parsed={"question": "Q?", "answer_span": "S"}))
    gen = LLMQuestionGenerator(fake.client())
    out = gen.generate_questions([{"text": "内容"}])
    assert out == [{"question": "Q?", "answer_span": "S"}]
    # 幂等键沿用 qe: 前缀（sha256 内容键）
    assert fake.submits[0]["idempotency_key"].startswith("qe:")
    assert fake.submits[0]["pipeline_stage"] == "mining_enrichment"


def test_summarizer_failure_propagates():
    """语义锁定：summary 适配器不吞错——异常上抛 → handler FALLBACK degraded。"""
    fake = _FakeLlm(status="failed")
    summarizer = LLMSummarizer(fake.client())
    with pytest.raises(RuntimeError, match="task-1"):
        summarizer.summarize("标题", ["片段一", "片段二"])


def test_summarizer_success_uses_summary_stage_key():
    fake = _FakeLlm(result=_result(text="摘要"))
    summarizer = LLMSummarizer(fake.client())
    out = summarizer.summarize("标题", ["片段"])
    assert out == "摘要"
    assert fake.submits[0]["pipeline_stage"] == "mining_summary"
    assert fake.submits[0]["idempotency_key"].startswith("sum:")
