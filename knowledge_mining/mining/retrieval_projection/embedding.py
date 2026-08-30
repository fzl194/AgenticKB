"""embedding 执行层（批次8 M4，24 号 §5.7）.

按 policy 分组批量嵌入、冻结每条 provenance、快照级替换暂存。
不做 pipeline 级 mode；一个节点内按 representation 分策略执行。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrievalRepresentation,
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

        representations: tuple[RetrievalRepresentation, ...] = run_sync(
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
            # 完整性硬校验：zip 截断会让空占位向量以 NULL 落库并虚报
            # dense_ready——数量不符/空向量一律显式失败，由算子 FAILED
            # 暴露供应商契约破坏，可重试。
            if len(embedded) != len(records):
                raise RuntimeError(
                    f"embedding provider returned {len(embedded)} vectors "
                    f"for {len(records)} inputs"
                )
            meta_dimension = int(meta.get("dimension", 0) or 0)
            for idx, vector in enumerate(embedded):
                if not vector:
                    raise RuntimeError(
                        f"embedding provider returned an empty vector "
                        f"at index {idx}"
                    )
                vectors[idx] = list(vector)
                # provider 未实现 describe() 时以首个真实向量长度为准（维度>0 才合法）
                if meta_dimension <= 0 and idx == 0:
                    meta = {**meta, "dimension": len(vector)}
            # 统一回填 dimension（describe 缺失时）
            effective_dim = int(meta.get("dimension", 0) or 0)
            if effective_dim > 0:
                records = [
                    EmbeddingRecord(
                        embedding_id=r.embedding_id,
                        representation_id=r.representation_id,
                        strategy=r.strategy,
                        strategy_input=r.strategy_input,
                        input_hash=r.input_hash,
                        policy_version=r.policy_version,
                        provider=r.provider,
                        model=r.model,
                        model_version=r.model_version,
                        dimension=effective_dim,
                        context_group_hash=r.context_group_hash,
                        fallback_from=r.fallback_from,
                    )
                    for r in records
                ]

        written = run_sync(
            self._embeddings.replace_for_snapshot(
                snapshot_id,
                tuple(records),
                policy.version,
                document_key=snapshot_id,
                # 向量本体与 records 平行传入：PG 实现落 embedding_vector_vec，
                # memory 实现忽略（M5 前仅暂存 provenance）。
                vectors=tuple(vectors),
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
