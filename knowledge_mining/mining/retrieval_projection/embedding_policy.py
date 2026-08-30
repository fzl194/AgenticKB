"""版本化 embedding policy（批次8 M4，24 号 §5.7）.

一个 embedding 节点、按 representation 分策略：
- 策略枚举 ``skip | isolated | structural | contextualized | late_chunking``；
- 默认矩阵 versioned 固定；用户参数按 representation_type 覆盖；
- provider capability 不满足且无显式 fallback → 显式失败，不静默换策略。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrievalRepresentation,
)

STRATEGIES = ("skip", "isolated", "structural", "contextualized", "late_chunking")

# 24 号 §5.7 默认矩阵（versioned default）
DEFAULT_POLICY_VERSION = "emb-policy-1"
_DEFAULT_MATRIX: Mapping[str, str] = {
    "prose": "structural",
    "section": "structural",
    "table": "structural",
    "table_row": "structural",
    "list_group": "structural",
    "figure_caption": "structural",
    "document": "isolated",
    "code_block": "isolated",
    "formula": "isolated",
    "query_alias": "isolated",
    "summary_alias": "isolated",
}
# 整文档/超长 raw section 默认 skip（本批没有 whole-document 型表示，规则留位）
_SKIP_WHEN = ("whole_document", "raw_section")

# provider 必须显式声明支持的高阶策略；structural/isolated/skip 为基线能力
BASELINE_CAPABILITIES = frozenset({"skip", "isolated", "structural"})
ADVANCED_STRATEGIES = frozenset({"contextualized", "late_chunking"})


@dataclass(frozen=True)
class StrategyDecision:
    strategy: str
    fallback_from: str | None = None


@dataclass(frozen=True)
class EmbeddingPolicy:
    version: str
    matrix: Mapping[str, str]
    overrides: Mapping[str, str]
    fallbacks: Mapping[str, str]

    def with_overrides(
        self,
        overrides: Mapping[str, str],
        *,
        fallbacks: Mapping[str, str] | None = None,
    ) -> "EmbeddingPolicy":
        merged_overrides = {**self.overrides, **overrides}
        merged_fallbacks = {**self.fallbacks, **(fallbacks or {})}
        version = self.version
        if merged_overrides or merged_fallbacks:
            version = f"{self.version}+override"
        return EmbeddingPolicy(
            version=version,
            matrix=self.matrix,
            overrides=merged_overrides,
            fallbacks=merged_fallbacks,
        )

    def _raw_strategy(self, representation: RetrievalRepresentation) -> str:
        override = self.overrides.get(representation.representation_type)
        if override:
            return override
        if representation.content_type in _SKIP_WHEN:
            return "skip"
        return self.matrix.get(representation.representation_type, "isolated")

    def decide(
        self,
        representation: RetrievalRepresentation,
        *,
        capabilities: frozenset[str] | None = None,
    ) -> StrategyDecision:
        raw = self._raw_strategy(representation)
        caps = capabilities if capabilities is not None else BASELINE_CAPABILITIES
        if raw in caps:
            return StrategyDecision(strategy=raw)
        fallback = self.fallbacks.get(representation.representation_type)
        if fallback:
            if fallback not in caps:
                raise ValueError(
                    f"embedding strategy fallback '{fallback}' for "
                    f"{representation.representation_type} is also unsupported "
                    f"by provider (capabilities={sorted(caps)})"
                )
            return StrategyDecision(strategy=fallback, fallback_from=raw)
        raise ValueError(
            f"embedding strategy '{raw}' for representation_type "
            f"'{representation.representation_type}' is not supported by the "
            f"embedding provider (capabilities={sorted(caps)}); configure an "
            f"explicit fallback or use a baseline strategy"
        )

    def strategy_for(
        self,
        representation: RetrievalRepresentation,
        *,
        capabilities: frozenset[str] | None = None,
    ) -> str:
        return self.decide(representation, capabilities=capabilities).strategy


def default_policy() -> EmbeddingPolicy:
    return EmbeddingPolicy(
        version=DEFAULT_POLICY_VERSION,
        matrix=dict(_DEFAULT_MATRIX),
        overrides={},
        fallbacks={},
    )


def embedding_input(
    representation: RetrievalRepresentation, strategy: str
) -> str | None:
    """按策略构造模型输入（§5.7：有界确定性上下文，非生成式）."""
    if strategy == "skip":
        return None
    if strategy == "isolated":
        return representation.content_text
    if strategy == "structural":
        context = representation.structural_context
        if context:
            return f"{context}\n{representation.content_text}"
        return representation.content_text
    # contextualized / late_chunking：依赖 provider 能力，输入构造属
    # provider 侧契约（本批无支持者，由 capability 校验挡在 decide 层）
    return representation.content_text


def policy_from_params(params: Mapping[str, object]) -> EmbeddingPolicy:
    policy = default_policy()
    overrides = params.get("strategyOverrides") or {}
    fallbacks = params.get("strategyFallbacks") or {}
    if overrides or fallbacks:
        policy = policy.with_overrides(
            {str(k): str(v) for k, v in overrides.items()},
            fallbacks={str(k): str(v) for k, v in fallbacks.items()},
        )
    return policy


__all__ = [
    "ADVANCED_STRATEGIES",
    "BASELINE_CAPABILITIES",
    "DEFAULT_POLICY_VERSION",
    "EmbeddingPolicy",
    "STRATEGIES",
    "StrategyDecision",
    "default_policy",
    "embedding_input",
    "policy_from_params",
]
