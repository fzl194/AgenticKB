"""embedding 执行层（批次8 M4，24 号 §5.7）.

按 policy 分组批量嵌入、冻结每条 provenance、快照级替换暂存。
不做 pipeline 级 mode；一个节点内按 representation 分策略执行。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrieRepresentation,
)
from knowledge_mining.mining.retrieval_projection.embedding_policy import (
    EmbeddingPolicy,
    embedding_input,
    policy_from_params,
)


@dataclass(frozen=True)
class EmbeddingRecord:
    """一条向量派生资产的完整 provenance（§5.7 冻结要求）."""

    embedding_id: str
    representation_id: str
    strategy: str
    strategy_input: str
    input_hash: str
    policy_version: str
    provider: str
    model: str
    model_version: str
    dimension: int
    context_group_hash: str
    fallback_from: str | None = None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingFacade:
    """同步门面：读表示暂存 → policy 分组 → embed_batch → 记录暂存."""

    def __init__(
        self,
        *,
        representation_store: Any,
        embedding_store: Any,
        generator: Any,
    ) -> None:
        self._representations = representation_store
        self._embeddings = embedding_store
        self._generator = generator

    def embed_for_snapshot(
        self, *, snapshot_id: str | None, params: Mapping[str, Any]
    ) -> Any:
        from types import SimpleNamespace

        if not snapshot_id:
            return SimpleNamespace(records=[], skipped=0)
        policy: EmbeddingPolicy = policy_from_params(params)
        capabilities = frozenset(
            getattr(self._generator, "capabilities", None)
            or ("skip", "isolated", "structural")
        )
        describe = getattr(self._generator, "describe", None)
        meta = describe() if describe else {
            "provider": "unknown", "model": "unknown",
            "version": "unknown", "dimension": 0,
        }

        from .async_bridge import run_sync

        representations: tuple[RetrieRepresentation, ...] = run_sync(
            self._representations.list_for_snapshot(snapshot_id)
        )
        records: list[EmbeddingRecord] = []
        vectors: list[list[float]] = []
        skipped = 0
        for representation in representations:
            decision = policy.decide(representation, capabilities=capabilities)
            model_input = embedding_input(representation, decision.strategy)
            if model_input is None:
                skipped += 1
                continue
            records.append(
                EmbeddingRecord(
                    embedding_id=f"{snapshot_id}:{representation.representation_id}",
                    representation_id=representation.representation_id,
                    strategy=decision.strategy,
                    strategy_input=model_input,
                    input_hash=_hash(model_input),
                    policy_version=policy.version,
                    provider=str(meta.get("provider", "unknown")),
                    model=str(meta.get("model", "unknown")),
                    model_version=str(meta.get("version", "unknown")),
                    dimension=int(meta.get("dimension", 0)),
                    context_group_hash=_hash(
                        representation.context_group_id or representation.representation_id
                    ),
                    fallback_from=decision.fallback_from,
                )
            )
            vectors.append([])  # 占位对齐，真向量由 embed_batch 批量回填

        if records:
            inputs = [record.strategy_input for record in records]
            embedded = self._generator.embed_batch(inputs) or []
            for record, vector in zip(records, embedded):
                vectors[records.index(record)] = list(vector)

        written = run_sync(
            self._embeddings.replace_for_snapshot(
                snapshot_id,
                tuple(records),
                policy.version,
                document_key=snapshot_id,
            )
        ) if records else 0
        return SimpleNamespace(
            records=tuple(records),
            vectors=tuple(vectors),
            skipped=skipped,
            written=written,
            policy_version=policy.version,
        )


__all__ = ["EmbeddingFacade", "EmbeddingRecord"]
