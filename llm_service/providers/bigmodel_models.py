from __future__ import annotations

import asyncio

import httpx

from llm_service.providers.model_base import ModelProviderError
from llm_service.providers.utils import extract_doc_text


class BigModelProvider:
    """BigModel modality provider for embeddings and rerank."""

    def __init__(
        self,
        *,
        embedding_api_key: str = "",
        embedding_url: str = "https://open.bigmodel.cn/api/paas/v4/embeddings",
        embedding_model: str = "embedding-3",
        embedding_timeout: int = 60,
        embedding_bypass_proxy: bool = False,
        embedding_headers: dict | None = None,
        # BUG-01：MaaS/bge-m3 等网关的 Pydantic 模型 extra_forbidden，
        # payload 带 dimensions 会 HTTP 200 + 空 data（0 向量死信）。
        # 默认 True 保持智谱 embedding-3 行为（需 dimensions 压到 1024）；
        # 不接受该参数的网关在配置里设 embedding.send_dimensions: false。
        embedding_send_dimensions: bool = True,
        rerank_api_key: str = "",
        rerank_url: str = "https://open.bigmodel.cn/api/paas/v4/rerank",
        rerank_model: str = "",
        rerank_timeout: int = 60,
        rerank_bypass_proxy: bool = False,
        rerank_headers: dict | None = None,
    ) -> None:
        self._embedding_api_key = embedding_api_key
        self._embedding_url = embedding_url.rstrip("/")
        self._embedding_model = embedding_model
        self._embedding_headers = embedding_headers or {}
        self._embedding_send_dimensions = embedding_send_dimensions
        self._rerank_api_key = rerank_api_key
        self._rerank_url = rerank_url.rstrip("/")
        self._rerank_model = rerank_model
        self._rerank_headers = rerank_headers or {}

        # Per-capability timeout. Build a shared client with the longer of the two.
        self._embedding_timeout = embedding_timeout
        self._rerank_timeout = rerank_timeout
        max_timeout = max(self._embedding_timeout, self._rerank_timeout)
        transport = httpx.AsyncHTTPTransport() if embedding_bypass_proxy else None
        self._client = httpx.AsyncClient(
            transport=transport, timeout=max_timeout, trust_env=not embedding_bypass_proxy
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self, api_key: str, capability: str) -> dict[str, str]:
        if not api_key:
            raise ModelProviderError(
                "not_configured",
                f"BigModel API key is not configured for {capability}",
            )
        extra = self._embedding_headers if capability == "embedding" else self._rerank_headers
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **extra,
        }

    async def _post(
        self,
        url: str,
        api_key: str,
        capability: str,
        payload: dict,
    ) -> dict:
        try:
            resp = await self._client.post(
                url,
                json=payload,
                headers=self._headers(api_key, capability),
            )
        except httpx.TimeoutException as e:
            raise ModelProviderError("timeout", str(e)) from e
        except httpx.ConnectError as e:
            raise ModelProviderError("connection_error", str(e)) from e

        if resp.status_code == 429:
            raise ModelProviderError("rate_limited", resp.text)
        if resp.status_code >= 500:
            raise ModelProviderError("server_error", f"HTTP {resp.status_code}: {resp.text}")
        if resp.status_code >= 400:
            raise ModelProviderError("client_error", f"HTTP {resp.status_code}: {resp.text}")
        try:
            return resp.json()
        except Exception as e:
            raise ModelProviderError(
                "invalid_response", f"Non-JSON response from provider: {e}"
            ) from e

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> dict:
        payload: dict = {
            "model": model or self._embedding_model,
            "input": texts,
        }
        # BUG-01：send_dimensions=False 的网关（MaaS bge-m3 网关
        # extra_forbidden，带 dimensions 即 200+空 data）绝不写入 payload；
        # dimensions 仍由 worker._validate_embedding_result 做本地校验。
        if dimensions is not None and self._embedding_send_dimensions:
            payload["dimensions"] = dimensions
        data = await self._post(
            self._embedding_url,
            self._embedding_api_key,
            "embedding",
            payload,
        )
        return {
            "model": payload["model"],
            "data": data.get("data", []),
            "usage": data.get("usage"),
        }

    # Per-document character cap. Documents longer than this are truncated to
    # keep a single rerank call within the provider's input length limit.
    _RERANK_DOC_MAX_CHARS = 4096

    # Maximum documents per single rerank API call.
    _RERANK_BATCH_SIZE = 128

    # Maximum total characters (query + docs) per single rerank API call.
    # Provider rejects with code 1214 ("query+document超长") above ~165KB;
    # 28000 is a conservative cap validated by prior production runs.
    _RERANK_BATCH_CHARS = 28000

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
    ) -> dict:
        used_model = model or self._rerank_model
        effective_top_n = top_n if top_n is not None else len(documents)

        # 1) Truncate each document to _RERANK_DOC_MAX_CHARS.
        #    We keep a sidecar of original (untruncated) texts for index mapping,
        #    because the API echoes back truncated text we sent, not the original.
        originals = list(documents)
        truncated = [
            (d if (d is None or len(d) <= self._RERANK_DOC_MAX_CHARS)
             else d[: self._RERANK_DOC_MAX_CHARS])
            for d in documents
        ]

        # 2) Split into batches that respect BOTH per-batch doc-count cap AND
        #    per-batch total-chars cap. Either limit being exceeded opens a new batch.
        batches = self._split_batches(
            truncated,
            max_count=self._RERANK_BATCH_SIZE,
            max_chars=self._RERANK_BATCH_CHARS,
            query_len=len(query or ""),
        )

        # 3) Single batch — direct call, API index is valid because batch == input.
        if len(batches) == 1:
            data = await self._rerank_call(
                query, batches[0], used_model, top_n=effective_top_n
            )
            results = self._rebuild_indices(
                data.get("results", []), truncated, ignore_api_index=False
            )
            # Restore original document text in the result items.
            for r in results:
                idx = r.get("index", -1)
                if 0 <= idx < len(originals):
                    r["document"] = {"text": originals[idx]}
            return {
                "model": used_model,
                "results": results,
                "usage": data.get("usage"),
            }

        # 4) Multi-batch — dispatch all batches concurrently.
        coros = [
            self._rerank_call(query, batch, used_model, top_n=len(batch))
            for batch in batches
        ]
        batch_responses = await asyncio.gather(*coros, return_exceptions=True)

        all_results: list[dict] = []
        total_usage: dict[str, int | float] = {}
        usage_seen = False
        for resp in batch_responses:
            if isinstance(resp, Exception):
                # Surface the first provider error; whole rerank fails upstream.
                raise resp
            all_results.extend(resp.get("results", []))
            usage = resp.get("usage")
            if isinstance(usage, dict):
                usage_seen = True
                for k, v in usage.items():
                    if isinstance(v, (int, float)):
                        total_usage[k] = total_usage.get(k, 0) + v

        # 5) Merge by score across batches, take top_n.
        all_results.sort(
            key=lambda x: float(x.get("relevance_score", 0.0)), reverse=True
        )
        all_results = all_results[:effective_top_n]

        # 6) Batch-local indices are not globally valid → force text-match against
        #    the truncated list (which is what the API saw). Then map back to originals.
        all_results = self._rebuild_indices(
            all_results, truncated, ignore_api_index=True
        )
        for r in all_results:
            idx = r.get("index", -1)
            if 0 <= idx < len(originals):
                r["document"] = {"text": originals[idx]}

        return {
            "model": used_model,
            "results": all_results,
            "usage": total_usage if usage_seen else None,
        }

    async def _rerank_call(
        self,
        query: str,
        documents: list[str],
        model: str,
        *,
        top_n: int,
    ) -> dict:
        payload = {
            "model": model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
            "return_documents": True,
        }
        return await self._post(self._rerank_url, self._rerank_api_key, "rerank", payload)

    @staticmethod
    def _split_batches(
        docs: list[str],
        *,
        max_count: int,
        max_chars: int,
        query_len: int,
    ) -> list[list[str]]:
        """Split docs into batches so each batch fits BOTH doc-count and char budget.

        - A batch is closed when adding the next doc would exceed ``max_count``
          documents OR ``max_chars - query_len`` total characters.
        - No per-doc truncation happens here (callers must truncate upstream).
        - A single doc larger than the per-batch char budget is placed in its own
          batch; the provider may reject it, but other docs are unaffected.
        """
        budget_for_docs = max(max_chars - query_len, 8000)

        batches: list[list[str]] = []
        current: list[str] = []
        current_len = 0
        for d in docs:
            dlen = len(d) if d else 0
            if current and (
                len(current) >= max_count or current_len + dlen > budget_for_docs
            ):
                batches.append(current)
                current = []
                current_len = 0
            current.append(d)
            current_len += dlen
        if current:
            batches.append(current)
        return batches or [list(docs)]

    @staticmethod
    def _rebuild_indices(
        results: list[dict],
        all_documents: list[str],
        *,
        ignore_api_index: bool,
    ) -> list[dict]:
        """Reconstruct global document index from document text.

        rerank-pro omits "index"; we map result.document text back to the original
        documents list. When ignore_api_index=True (multi-batch merge), any local
        index returned by the API is discarded because it is batch-local.
        """
        doc_to_idx: dict[str, list[int]] = {}
        for i, d in enumerate(all_documents):
            doc_to_idx.setdefault(d, []).append(i)
        used_indices: set[int] = set()
        for item in results:
            if not ignore_api_index and "index" in item:
                used_indices.add(item["index"])
                continue
            doc_text = extract_doc_text(item.get("document"))
            candidates = doc_to_idx.get(doc_text, [])
            # Pick first unused index to handle duplicate documents
            for ci in candidates:
                if ci not in used_indices:
                    item["index"] = ci
                    used_indices.add(ci)
                    break
            else:
                item["index"] = -1
        return results
