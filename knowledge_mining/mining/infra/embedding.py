"""Embedding generator protocol and implementations.

Provides:
- EmbeddingGenerator Protocol (hot-pluggable)
- LLMServiceEmbeddingGenerator: calls llm_service embedding endpoint (sync)
- LLMServiceAsyncEmbeddingGenerator: llm_service async task channel
  (排队削峰 + worker 并发闸门 + 任务级重试——大文档主路径)
- NoOpEmbeddingGenerator: fallback when embedding is not configured
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Protocol, runtime_checkable

import httpx

from .llm_task_client import LlmTaskClient, LlmTaskError

logger = logging.getLogger(__name__)


@runtime_checkable
class EmbeddingGenerator(Protocol):
    """Protocol for generating text embeddings."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]: ...


class NoOpEmbeddingGenerator:
    """Fallback: returns empty embeddings when embedding is not configured."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        return []


class LLMServiceEmbeddingGenerator:
    """Embedding client backed by llm_service model endpoint.

    Model name and dimensions are managed by llm_service — caller only sends text.
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

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        payload: dict[str, Any] = {
            "input": texts,
            "caller_service": "mining",
            "knowledge_domain": self._knowledge_domain or "unknown",
            "pipeline_stage": "embedding",
        }
        try:
            with httpx.Client(base_url=self._base_url, timeout=self._timeout, proxy=None, trust_env=False) as client:
                resp = client.post("/api/v1/models/embeddings", json=payload)
                resp.raise_for_status()
                data = resp.json()
            results = data.get("data", [])
            results.sort(key=lambda x: x.get("index", 0))
            return [item.get("embedding", []) for item in results]
        except Exception as e:
            logger.warning("LLM service embedding call failed: %s", e)
            # 保留原始异常类型与消息给 document handler / Run 诊断。返回 []
            # 会把认证、限流、超时全部伪装成“供应商返回 0 条”，并可能让
            # 下游误读旧 staging 向量。
            raise

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_result = self.embed(batch)
            if len(batch_result) != len(batch):
                raise RuntimeError(
                    "LLM service embedding batch mismatch: "
                    f"expected {len(batch)}, got {len(batch_result)}"
                )
            all_embeddings.extend(batch_result)
        return all_embeddings


class LLMServiceAsyncEmbeddingGenerator:
    """EmbeddingGenerator 协议的异步任务通道实现（llm_call_mode=async，默认）。

    替代同步直连的主 bug 修复：同步 POST /models/embeddings 绕过 llm_service
    worker（无排队、无并发闸门、无重试），单文档几千检索单元 32 条/批串行
    直连会把内网 embedding 端点打到 429/超时 → 整文档 FAILED。

    关键约束：llm_service worker 对一个任务一次 ``provider.embed(整个
    texts)``（任务内不分批），因此这里必须按 ``batch_size`` 自分批提交，
    批间由 worker 并发（llm_service.yaml worker.concurrency）吸收。等待是
    算子内阻塞轮询（用户拍板），单一 deadline 覆盖全部批次。

    失败语义与同步版一致：任何失败（提交失败 / failed / dead_letter /
    超时 / 条数不符 / 空向量）raise → embedding_handler FAILED → 文档失败，
    下次挖掘增量 RETRY 兜底。
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8900",
        poll_interval: float = 1.0,
        wait_timeout: float = 900.0,
        max_attempts: int = 3,
        batch_size: int = 32,
        knowledge_domain: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        self._client = LlmTaskClient(
            base_url=base_url,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
            transport=transport,
        )
        self._max_attempts = max_attempts
        self._batch_size = batch_size
        self._knowledge_domain = knowledge_domain

    def close(self) -> None:
        """Release the underlying task-channel client (end of run)."""
        self._client.close()

    @staticmethod
    def _batch_key(batch: list[str]) -> str:
        """内容哈希幂等键：同内容同任务（文档共享快照/重跑天然去重）。

        ``v1`` 是请求协议版本——批切分/键构成变化时递增，避免跨协议复用。
        模型/维度等服务端契约由 llm_service 的 request fingerprint 校验
        （同 key 不同请求不复用）。dead_letter 不在幂等查找范围（只匹配
        succeeded/running/queued），重提交会建新任务——正是期望的行为。
        """
        digest = hashlib.sha256("\x1f".join(batch).encode("utf-8")).hexdigest()[:24]
        return f"emb:v1:{digest}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        """协议完备性保留：单任务提交全部 texts。

        生产路径 EmbeddingFacade 只调 ``embed_batch``；本方法语义与同步版
        ``embed`` 对齐（单次调用全量）。
        """
        if not texts:
            return []
        results = self._run([texts])
        return results[0]

    def embed_batch(
        self, texts: list[str], batch_size: int | None = None,
    ) -> list[list[float]]:
        """按批提交并回填。省略 ``batch_size`` 时用构造值。"""
        if not texts:
            return []
        size = batch_size if batch_size is not None else self._batch_size
        if size < 1:
            raise ValueError(f"batch_size must be >= 1, got {size}")
        batches = [texts[i : i + size] for i in range(0, len(texts), size)]
        results = self._run(batches)
        out: list[list[float]] = []
        for vectors in results:
            out.extend(vectors)
        return out

    def _run(self, batches: list[list[str]]) -> list[list[list[float]]]:
        """提交全部批次（相同内容哈希的批只提交一次）→ 单一 deadline 等待 → 按批回填。"""
        key_to_ids: dict[str, list[str]] = {}
        batch_keys: list[str] = []
        for batch in batches:
            key = self._batch_key(batch)
            batch_keys.append(key)
            if key in key_to_ids:
                continue
            try:
                task_id = self._client.submit_embedding(
                    batch,
                    idempotency_key=key,
                    max_attempts=self._max_attempts,
                    knowledge_domain=self._knowledge_domain,
                )
            except LlmTaskError as e:
                raise RuntimeError(f"embedding task submit failed for {key}: {e}") from e
            key_to_ids[key] = [task_id]

        all_task_ids = [tid for ids in key_to_ids.values() for tid in ids]
        entries = self._client.wait_for_tasks(all_task_ids)

        results: list[list[list[float]]] = []
        for key, batch in zip(batch_keys, batches):
            task_id = key_to_ids[key][0]
            entry = entries.get(task_id) or {"task_id": task_id, "status": "timeout"}
            results.append(self._vectors_of(task_id, entry, key, expected=len(batch)))
        return results

    def _vectors_of(
        self, task_id: str, entry: dict[str, Any], key: str, *, expected: int,
    ) -> list[list[float]]:
        """单批结果提取：succeeded → data 按 index 排序；其余终态显式失败。"""
        status = str(entry.get("status"))
        if status != "succeeded":
            err = entry.get("error") or {}
            raise RuntimeError(
                f"embedding task {task_id} ({key}) ended as {status}: "
                f"{err.get('error_type')}: {err.get('error_message')}"
            )
        result = entry.get("result") or {}
        data = result.get("parsed_output") or {}
        items = data.get("data")
        if not isinstance(items, list):
            raise RuntimeError(
                f"embedding task {task_id} ({key}) returned no data items"
            )
        ordered = sorted(
            items,
            key=lambda x: x.get("index", 0) if isinstance(x, dict) else 0,
        )
        vectors: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or not vector:
                raise RuntimeError(
                    f"embedding task {task_id} ({key}) returned an empty vector"
                )
            vectors.append([float(v) for v in vector])
        if len(vectors) != expected:
            # 与 EmbeddingFacade 的硬校验双保险：provider 短返不得静默对齐。
            raise RuntimeError(
                f"embedding task {task_id} ({key}) returned {len(vectors)} vectors "
                f"for {expected} inputs"
            )
        return vectors
