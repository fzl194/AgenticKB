"""Embedding generator protocol and implementations.

Provides:
- EmbeddingGenerator Protocol (hot-pluggable)
- LLMServiceEmbeddingGenerator: calls llm_service embedding endpoint
- NoOpEmbeddingGenerator: fallback when embedding is not configured
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import httpx

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
