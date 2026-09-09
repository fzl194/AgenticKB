"""M3 生产接线（29号收尾）：llm_service 生成客户端与两算子适配器.

- ``GenerationClient`` Protocol：同步 /execute 与异步任务通道客户端的
  共同接口（适配器只依赖它）；
- ``LLMServiceGenerationClient``：同步调用 llm_service ``/api/v1/execute``
  （llm_call_mode=sync 回退路径；任务留痕/幂等键由 llm_service 任务系统承担）；
- ``LLMServiceAsyncGenerationClient``：走 ``/api/v1/tasks`` 异步任务通道
  （llm_call_mode=async，默认）——worker 并发闸门 + 任务级退避重试；
- ``LLMQuestionGenerator``：适配 ``QueryExpansionFacade`` 的
  ``generate_questions(items)`` 契约（逐项 question+answer_span / SKIP）；
- ``LLMSummarizer``：适配 ``HierarchicalSummaryFacade`` 的
  ``summarize(title, texts)`` 契约。

失败语义（24号 §5.5/§5.6）：调用失败/解析失败按 SKIP 计数，算子整体只
degraded 增强能力，不阻断基础资产。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Protocol, runtime_checkable

import httpx

from knowledge_mining.mining.infra.llm_task_client import LlmTaskClient, LlmTaskError

logger = logging.getLogger(__name__)

#: prompt 版本冻结进 provenance（29号 M3 接线：模型/prompt 可追溯）。
QUESTION_PROMPT_VERSION = "qe-doc2query-1"
SUMMARY_PROMPT_VERSION = "hier-summary-1"
# Internal adapter outcome.  It is deliberately distinct from business SKIP so
# the facade can freeze provider degradation accurately.
LLM_FAILURE = "__LLM_PROVIDER_FAILURE__"

_QUESTION_SYSTEM = (
    "你是检索增强数据标注器。给定一段知识库原文，为它生成一个用户真实会问的"
    '检索问题，并给出答案在原文中的连续片段。只输出 JSON 对象：'
    '{"question": "...", "answer_span": "..."}。'
    "answer_span 必须逐字取自原文（不要改写、不要拼接）；"
    '若该片段不适合生成问题（太短/纯导航/无事实），输出 {"skip": true}。'
)

_SUMMARY_SYSTEM = (
    "你是知识库摘要器。给定标题与若干内容片段（可能含子章节摘要），"
    "输出 2-4 句客观摘要：只陈述片段中的事实，不引入外部信息，"
    "不使用列表和标题格式，直接输出摘要正文。"
)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@runtime_checkable
class GenerationClient(Protocol):
    """同步 /execute 与异步任务通道客户端的共同接口。

    两个适配器（Question/Summary）只依赖本协议；组合根按
    ``MiningConfig.llm_call_mode`` 注入实现。
    """

    def execute(
        self,
        messages: list[dict[str, str]],
        *,
        expected_output_type: str = "text",
        idempotency_key: str | None = None,
        pipeline_stage: str = "mining_enrichment",
    ) -> Any: ...


class LLMServiceGenerationClient:
    """llm_service 同步生成客户端（/execute）。

    ``expected_output_type`` 透传 llm_service 的输出解析器：
    ``json_object`` 返回解析后的 dict，``text`` 返回原文串。
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8900",
        timeout: int = 60,
        knowledge_domain: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._knowledge_domain = knowledge_domain

    def execute(
        self,
        messages: list[dict[str, str]],
        *,
        expected_output_type: str = "text",
        idempotency_key: str | None = None,
        pipeline_stage: str = "mining_enrichment",
    ) -> Any:
        payload: dict[str, Any] = {
            "caller_service": "mining",
            "knowledge_domain": self._knowledge_domain or "unknown",
            "pipeline_stage": pipeline_stage,
            "messages": messages,
            "expected_output_type": expected_output_type,
            "max_attempts": 1,
        }
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        with httpx.Client(
            base_url=self._base_url, timeout=self._timeout,
            proxy=None, trust_env=False,
        ) as client:
            resp = client.post("/api/v1/execute", json=payload)
            resp.raise_for_status()
            data = resp.json()
        result = (data.get("data") or {})
        if result.get("status") != "succeeded":
            error = result.get("error") or {}
            raise RuntimeError(
                f"llm_service execute failed: "
                f"{error.get('error_type')}: {error.get('error_message')}"
            )
        payload_result = result.get("result") or {}
        if expected_output_type in ("json_object", "json_array"):
            parsed = payload_result.get("parsed_output")
            if isinstance(parsed, dict):
                return parsed
            # 解析失败但有文本：尽力正则兜底
            text = payload_result.get("text_output")
            if isinstance(text, str):
                match = _JSON_OBJECT_RE.search(text)
                if match:
                    return json.loads(match.group(0))
            raise RuntimeError("llm_service execute: json parse failed")
        text = payload_result.get("text_output")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("llm_service /execute returned empty output")
        return text


def _stable_key(prefix: str, text: str) -> str:
    return f"{prefix}:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]}"


class LLMServiceAsyncGenerationClient:
    """llm_service 异步任务通道生成客户端（/api/v1/tasks，默认）。

    结果映射与 :class:`LLMServiceGenerationClient` 逐分支对齐（json 正则
    兜底 / text 空串 raise / 失败 raise 含 task_id），适配器的失败语义
    （question→LLM_FAILURE 哨兵、summary→上抛 FALLBACK）不变。

    有意差异：同步 /execute 是 max_attempts=1 单发；本客户端默认 3 次退避
    重试（llm_service TaskManager 8^n 上限 120s）——429 场景要的韧性；
    ``llm_async_max_attempts: 1`` 可复原单发。
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8900",
        poll_interval: float = 1.0,
        wait_timeout: float = 300.0,
        max_attempts: int = 3,
        knowledge_domain: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = LlmTaskClient(
            base_url=base_url,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
            transport=transport,
        )
        self._max_attempts = max_attempts
        self._knowledge_domain = knowledge_domain

    def execute(
        self,
        messages: list[dict[str, str]],
        *,
        expected_output_type: str = "text",
        idempotency_key: str | None = None,
        pipeline_stage: str = "mining_enrichment",
    ) -> Any:
        try:
            task_id = self._client.submit_chat(
                messages,
                expected_output_type=expected_output_type,
                idempotency_key=idempotency_key,
                pipeline_stage=pipeline_stage,
                max_attempts=self._max_attempts,
                knowledge_domain=self._knowledge_domain,
            )
        except LlmTaskError as e:
            raise RuntimeError(f"llm_service task submit failed: {e}") from e

        entries = self._client.wait_for_tasks([task_id])
        entry = entries.get(task_id) or {"task_id": task_id, "status": "timeout"}
        status = str(entry.get("status"))
        if status != "succeeded":
            err = entry.get("error") or {}
            raise RuntimeError(
                f"llm_service task {task_id} ended as {status}: "
                f"{err.get('error_type')}: {err.get('error_message')}"
            )
        payload_result = entry.get("result") or {}
        if expected_output_type in ("json_object", "json_array"):
            parsed = payload_result.get("parsed_output")
            if isinstance(parsed, dict):
                return parsed
            # 解析失败但有文本：尽力正则兜底（与同步版一致）
            text = payload_result.get("text_output")
            if isinstance(text, str):
                match = _JSON_OBJECT_RE.search(text)
                if match:
                    return json.loads(match.group(0))
            raise RuntimeError("llm_service task: json parse failed")
        text = payload_result.get("text_output")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("llm_service task returned empty output")
        return text


class LLMQuestionGenerator:
    """Doc2Query 适配器：items → 逐项 {question, answer_span} / "SKIP"。

    预算：每快照最多 ``max_items`` 个合格表示进入生成（其余 SKIP）——
    实验范式的成本护栏。
    """

    def __init__(self, client: GenerationClient, *,
                 max_items: int = 64) -> None:
        self._client = client
        self._max_items = max_items

    def generate_questions(self, items: list[dict[str, Any]]) -> list[Any]:
        out: list[Any] = []
        for idx, item in enumerate(items):
            if idx >= self._max_items:
                out.append("SKIP")
                continue
            text = str(item.get("text") or "")
            if not text:
                out.append("SKIP")
                continue
            try:
                obj = self._client.execute(
                    [
                        {"role": "system", "content": _QUESTION_SYSTEM},
                        {"role": "user", "content": text[:4000]},
                    ],
                    expected_output_type="json_object",
                    idempotency_key=_stable_key("qe", text),
                )
                if obj.get("skip"):
                    out.append("SKIP")
                    continue
                question = str(obj.get("question") or "").strip()
                span = str(obj.get("answer_span") or "").strip()
                if not question or not span:
                    out.append("SKIP")
                    continue
                out.append({"question": question, "answer_span": span})
            except Exception as exc:  # noqa: BLE001
                logger.warning("question generation failed: %s", exc)
                out.append(LLM_FAILURE)
        return out


class LLMSummarizer:
    """层级摘要适配器：summarize(title, texts) → 摘要正文 str。"""

    def __init__(self, client: GenerationClient) -> None:
        self._client = client

    def summarize(self, title: str, texts: list[str]) -> str:
        joined = "\n".join(t for t in texts if t and t.strip())[:8000]
        if not joined:
            raise RuntimeError("nothing to summarize")
        out = self._client.execute(
            [
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {
                    "role": "user",
                    "content": f"标题：{title}\n\n内容片段：\n{joined}",
                },
            ],
            expected_output_type="text",
            idempotency_key=_stable_key("sum", f"{title}\n{joined[:512]}"),
            pipeline_stage="mining_summary",
        )
        return str(out).strip()


__all__ = [
    "GenerationClient",
    "LLMQuestionGenerator",
    "LLMServiceAsyncGenerationClient",
    "LLMServiceGenerationClient",
    "LLMSummarizer",
    "LLM_FAILURE",
    "QUESTION_PROMPT_VERSION",
    "SUMMARY_PROMPT_VERSION",
]
