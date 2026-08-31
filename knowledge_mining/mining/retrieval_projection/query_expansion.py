"""query_expansion_generate 执行层（批次8 M3，24 号 §5.5）.

离线 Doc2Query 风格增强：对合格源表示生成 ``query_alias``（检索别名，
returnable=False，指回 canonical evidence）。资格门/answer_span 回源
校验/三层去重/SKIP 语义按 §5.5；LLM 失败只 degraded，不阻断基础资产。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

from knowledge_mining.mining.contracts.retrieval_projection import (
    PROJECTOR_VERSION,
    RetrievalRepresentation,
)
from knowledge_mining.mining.retrieval_projection.llm_generation import (
    LLM_FAILURE,
)

# 资格门默认（versioned；§5.5）：prose 归一化 ≥80 tokens；
# table_row 至少一个 header-value；其余类型默认关闭。
ELIGIBILITY_VERSION = "qe-eligibility-1"
_DEFAULT_ENABLED_TYPES = frozenset({"prose", "table_row", "list_group"})
_MIN_PROSE_CHARS = 80


def is_eligible(representation: RetrievalRepresentation) -> bool:
    if representation.representation_type not in _DEFAULT_ENABLED_TYPES:
        return False
    text = normalize_text(representation.content_text)
    if representation.representation_type == "prose":
        return len(text) >= _MIN_PROSE_CHARS
    return bool(text)


def normalize_text(text: str) -> str:
    """Unicode/空白归一化（answer_span 匹配与资格门共用）."""
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", normalized).strip()


def validate_answer_span(span: str, *, source_text: str) -> bool:
    """answer_span 必须在允许的源证据范围内归一化精确匹配."""
    return normalize_text(span) in normalize_text(source_text)


@dataclass(frozen=True)
class AliasDraft:
    question: str
    canonical_evidence_id: str
    document: str
    answer_span_valid: bool


def dedup_aliases(drafts: list[AliasDraft]) -> list[AliasDraft]:
    """三层去重：target 内 / 文档内 / canonical evidence（§5.5-10）."""
    seen_questions: set[str] = set()
    kept: list[AliasDraft] = []
    for draft in drafts:
        question = normalize_text(draft.question)
        if not question or question in seen_questions:
            continue
        seen_questions.add(question)
        kept.append(draft)
    return kept


def _alias_representation(
    base: RetrievalRepresentation, question: str, ordinal: int
) -> RetrievalRepresentation:
    return RetrievalRepresentation(
        representation_id=f"{base.representation_id}:alias:{ordinal}",
        representation_type="query_alias",
        content_type="query_alias",
        content_text=question,
        structural_context=base.structural_context,
        target_type=base.target_type,
        target_ref=base.target_ref,
        canonical_evidence_id=base.canonical_evidence_id,
        source_refs=({"representation_id": base.representation_id},),
        lexical_eligible=True,
        dense_eligible=True,
        returnable=False,  # 别名不是答案内容，命中必须回源
        facets=dict(base.facets),
        provenance={
            "projector": "query_expansion_generate",
            "projector_version": PROJECTOR_VERSION,
            "eligibility_version": ELIGIBILITY_VERSION,
            "source_representation": base.representation_id,
            "derived": True,
        },
    )


@dataclass(frozen=True)
class ExpansionOutcome:
    aliases: tuple[RetrievalRepresentation, ...]
    skipped: int
    invalid: int
    llm_failures: int
    degraded: bool


class QueryExpansionFacade:
    """同步门面：读表示暂存 → 资格门 → LLM → 校验去重 → 别名暂存."""

    def __init__(self, *, representation_store: Any, alias_store: Any, generator: Any,
                 max_aliases_per_target: int = 1) -> None:
        self._representations = representation_store
        self._aliases = alias_store
        self._generator = generator
        self._max_per_target = max_aliases_per_target

    def generate_for_snapshot(
        self, *, snapshot_id: str | None, params: Mapping[str, Any]
    ) -> ExpansionOutcome:
        if not snapshot_id:
            return ExpansionOutcome((), 0, 0, 0, False)

        from .async_bridge import run_sync

        # 27号审查修复：maxAliasesPerTarget 由算子参数真正生效（此前只有
        # 构造器默认，handler 校验后并未传进门面）。
        max_aliases = int(params.get("maxAliasesPerTarget", self._max_per_target))

        representations = run_sync(
            self._representations.list_for_snapshot(snapshot_id)
        )
        eligible = [rep for rep in representations if is_eligible(rep)]
        drafts: list[AliasDraft] = []
        skipped = invalid = llm_failures = 0
        if eligible:
            items = [
                {
                    "representation_id": rep.representation_id,
                    "text": rep.content_text,
                    "structural_context": rep.structural_context,
                }
                for rep in eligible
            ]
            try:
                raw_results = self._generator.generate_questions(items)
            except Exception:  # noqa: BLE001
                return ExpansionOutcome((), skipped, invalid, 1, True)
            for rep, raw in zip(eligible, raw_results):
                if raw == LLM_FAILURE:
                    llm_failures += 1
                    continue
                if raw is None or raw == "SKIP":
                    skipped += 1
                    continue
                if not isinstance(raw, Mapping):
                    invalid += 1
                    continue
                question = str(raw.get("question") or "").strip()
                span = str(raw.get("answer_span") or "")
                # 27号审查修复（§5.5「generation context 只帮助理解，不能
                # 扩大证据范围」）：answer_span 只对源表示原文校验——LLM
                # 自报的 source_text 不参与判定。
                if not question or not validate_answer_span(
                    span, source_text=rep.content_text
                ):
                    invalid += 1
                    continue
                drafts.append(
                    AliasDraft(
                        question=question,
                        canonical_evidence_id=rep.canonical_evidence_id,
                        document=str(rep.facets.get("document", "")),
                        answer_span_valid=True,
                    )
                )

        # 27号审查修复：per-target 上限按 canonical 真正计数（此前是全局
        # 截断 len(eligible)*max，单个 target 可占满全部名额）。
        per_target: dict[str, int] = {}
        capped: list[AliasDraft] = []
        for draft in dedup_aliases(drafts):
            used = per_target.get(draft.canonical_evidence_id, 0)
            if used >= max_aliases:
                continue
            per_target[draft.canonical_evidence_id] = used + 1
            capped.append(draft)
        by_canonical = {rep.canonical_evidence_id: rep for rep in eligible}
        aliases = tuple(
            _alias_representation(
                by_canonical[draft.canonical_evidence_id], draft.question, idx,
            )
            for idx, draft in enumerate(capped)
        )

        if self._aliases is not None:
            # 27号审查修复：alias 子集替换语义——不得整快照清空（若 alias
            # store 与主表示 store 同体会抹掉基础表示）。store 未实现该
            # 方法时回落整替（独立 alias store 的旧契约）。
            replace_aliases = getattr(
                self._aliases, "replace_aliases_for_snapshot", None
            )
            if replace_aliases is not None:
                run_sync(replace_aliases(
                    snapshot_id, aliases, PROJECTOR_VERSION,
                    document_key=snapshot_id,
                    alias_type="query_alias",
                ))
            elif aliases:
                run_sync(self._aliases.replace_for_snapshot(
                    snapshot_id, aliases, PROJECTOR_VERSION,
                    document_key=snapshot_id,
                ))
        degraded = llm_failures > 0
        return ExpansionOutcome(aliases, skipped, invalid, llm_failures, degraded)


def question_signature(question: str, source_hash: str) -> str:
    """幂等签名（§5.5）：source hash + normalized question."""
    return hashlib.sha256(
        f"{source_hash}:{normalize_text(question)}".encode("utf-8")
    ).hexdigest()


__all__ = [
    "ELIGIBILITY_VERSION",
    "AliasDraft",
    "ExpansionOutcome",
    "QueryExpansionFacade",
    "dedup_aliases",
    "is_eligible",
    "normalize_text",
    "question_signature",
    "validate_answer_span",
]
