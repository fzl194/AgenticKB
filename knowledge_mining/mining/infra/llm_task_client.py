"""挖掘侧异步任务通道低层客户端（llm_service /api/v1/tasks*）。

与 ``llm_client.LlmClient``（B 类模板任务，吞错返 None）不同，本客户端
服务 embedding / generation / image caption 三个同步残留点的异步化：
失败语义必须逐调用点保持（embedding 失败=文档 FAILED；generation 失败=
degraded），因此提交与终态失败都**上抛** :class:`LlmTaskError`（含 task_id
便于溯源），不吞错。

所有等待路径都带总超时（``wait_for_tasks``）；轮询中的瞬时 HTTP 错误容忍
到 deadline——与 llm_service 侧 lease 恢复的语义对齐：任务不会因为轮询方
抖动而丢失。批量状态查询用 POST /api/v1/tasks/batch-status（一次请求替代
N 次 GET，succeeded 任务内联 result）。
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .llm_client import CALLER_SERVICE, DEFAULT_BASE_URL, UNKNOWN_DOMAIN

logger = logging.getLogger(__name__)

#: 任务终态集合（llm_service TaskManager 语义）。
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "dead_letter", "cancelled"})


class LlmTaskError(RuntimeError):
    """任务通道失败（提交失败 / 终态失败 / 超时）。message 应含 task_id。"""


class LlmTaskClient:
    """Sync HTTP client for llm_service async task channel.

    Reuses a single httpx.Client across calls (same rationale as
    ``LlmClient``: high-frequency polling). Unlike ``LlmClient``, submit and
    terminal-failure raise instead of returning None — callers map failures to
    their own per-site semantics.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
        wait_timeout: float = 900.0,
        status_chunk: int = 200,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._wait_timeout = wait_timeout
        # batch-status endpoint caps task_ids at 256; chunk below the cap.
        self._status_chunk = min(status_chunk, 256)
        self._transport = transport
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=self._timeout, proxy=None, trust_env=False,
                transport=self._transport,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            self._client.close()
        self._client = None

    # ------------------------------------------------------------------
    # submit
    # ------------------------------------------------------------------

    def submit_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        expected_output_type: str | None = None,
        idempotency_key: str | None = None,
        pipeline_stage: str = "mining_enrichment",
        max_attempts: int = 3,
        priority: int = 100,
        model: str | None = None,
        knowledge_domain: str | None = None,
    ) -> str:
        """POST /api/v1/tasks（messages 直传，无需模板）。返回 task_id。

        :raises LlmTaskError: 提交失败（网络/5xx/校验）。
        """
        payload: dict[str, Any] = {
            "caller_service": CALLER_SERVICE,
            "knowledge_domain": knowledge_domain or UNKNOWN_DOMAIN,
            "pipeline_stage": pipeline_stage,
            "messages": messages,
            "max_attempts": max_attempts,
            "priority": priority,
        }
        if expected_output_type is not None:
            payload["expected_output_type"] = expected_output_type
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        if model is not None:
            payload["model"] = model
        return self._submit("/api/v1/tasks", payload)

    def submit_embedding(
        self,
        texts: list[str],
        *,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
        priority: int = 100,
        knowledge_domain: str | None = None,
    ) -> str:
        """POST /api/v1/tasks/embed。返回 task_id。

        max_attempts 必须显式传：EmbeddingTaskRequest 模型默认 2，挖掘
        场景要 3 次退避重试。worker 对一个任务一次 provider.embed(整个
        texts)（任务内不分批），调用方须自行控制单任务条数。

        :raises LlmTaskError: 提交失败（网络/5xx/校验）。
        """
        payload: dict[str, Any] = {
            "input": texts,
            "caller_service": CALLER_SERVICE,
            "knowledge_domain": knowledge_domain or UNKNOWN_DOMAIN,
            "pipeline_stage": "embedding",
            "max_attempts": max_attempts,
            "priority": priority,
        }
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        return self._submit("/api/v1/tasks/embed", payload)

    def _submit(self, path: str, payload: dict[str, Any]) -> str:
        try:
            resp = self._get_client().post(f"{self._base_url}{path}", json=payload)
            resp.raise_for_status()
            body = resp.json()
        except Exception as e:
            self.close()
            raise LlmTaskError(f"task submit to {path} failed: {e}") from e
        data = body.get("data", body) if isinstance(body, dict) else {}
        task_id = data.get("task_id") if isinstance(data, dict) else None
        if not task_id:
            raise LlmTaskError(f"task submit to {path} returned no task_id: {body!r:.200}")
        return str(task_id)

    # ------------------------------------------------------------------
    # status / wait
    # ------------------------------------------------------------------

    def batch_status(
        self, task_ids: list[str], *, include_results: bool = True,
    ) -> dict[str, dict]:
        """POST /api/v1/tasks/batch-status（超 status_chunk 自动分块）。

        返回 ``{task_id: entry}``（entry 含 status/task_type/result/error/
        attempt_count…）。not_found 的 id 不出现在返回里——由调用方按
        「未决」处理。

        :raises LlmTaskError: HTTP 错误（由 wait_for_tasks 容忍重试）。
        """
        entries: dict[str, dict] = {}
        ids = [t for t in dict.fromkeys(task_ids)]
        for start in range(0, len(ids), self._status_chunk):
            chunk = ids[start:start + self._status_chunk]
            payload = {"task_ids": chunk, "include_results": include_results}
            try:
                resp = self._get_client().post(
                    f"{self._base_url}/api/v1/tasks/batch-status", json=payload,
                )
                resp.raise_for_status()
                body = resp.json()
            except Exception as e:
                self.close()
                raise LlmTaskError(f"batch-status failed: {e}") from e
            data = body.get("data", {}) if isinstance(body, dict) else {}
            for entry in data.get("tasks", []):
                if isinstance(entry, dict) and entry.get("task_id"):
                    entries[str(entry["task_id"])] = entry
        return entries

    def wait_for_tasks(
        self,
        task_ids: list[str],
        *,
        timeout: float | None = None,
        poll_interval: float | None = None,
    ) -> dict[str, dict]:
        """带总超时的批量轮询（默认 self._wait_timeout）。

        返回 ``{task_id: entry}``；未在 deadline 内到终态的条目以
        ``status="timeout"`` 的占位 entry 返回。轮询中的 LlmTaskError
        （5xx/断连）不中断——睡一个间隔继续，直到 deadline：任务本身
        的存活由 llm_service 侧 lease 恢复负责，轮询方抖动不该误伤。
        """
        resolved: dict[str, dict] = {}
        pending = [t for t in dict.fromkeys(task_ids) if t]
        if not pending:
            return resolved
        interval = poll_interval if poll_interval is not None else self._poll_interval
        deadline = time.monotonic() + (timeout if timeout is not None else self._wait_timeout)

        while pending and time.monotonic() < deadline:
            try:
                entries = self.batch_status(list(pending))
            except LlmTaskError as e:
                logger.warning("batch-status poll error (will retry until deadline): %s", e)
                entries = {}
            still: list[str] = []
            for task_id in pending:
                entry = entries.get(task_id)
                if entry is not None and str(entry.get("status")) in _TERMINAL_STATUSES:
                    resolved[task_id] = entry
                else:
                    still.append(task_id)
            pending = still
            if pending:
                time.sleep(interval)

        for task_id in pending:
            resolved[task_id] = {
                "task_id": task_id, "status": "timeout",
                "error": {"error_type": "wait_timeout", "error_message": None},
            }
        return resolved


__all__ = ["LlmTaskClient", "LlmTaskError"]
