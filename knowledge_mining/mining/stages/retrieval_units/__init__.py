"""Retrieval units stage: build retrieval-ready units from segments.

v1.5 industrial-grade: LLM-driven quality assessment, no hardcoded thresholds.

Design principles (LlamaIndex SchemaLLMPathExtractor + GraphRAG + Anthropic Contextual Retrieval):
- LLM does intelligent content quality assessment (not regex + thresholds)
- Schema constrains LLM output (not if-else chains)
- Validate structure, prune invalid (not content quality scoring)
- raw_text units are the primary evidence layer (1:1 with segments)
- entity_card only for strong types from non-navigation segments
- generated_question: LLM decides whether to generate (empty array = skip)
- table_row units for per-row retrieval on structured tables
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import replace
from typing import Any

from knowledge_mining.mining.contracts.models import RawSegmentData, RetrievalUnitData
from knowledge_mining.mining.contracts.protocols import QuestionGenerator, Contextualizer
from knowledge_mining.mining.infra.text_utils import tokenize_for_search
from knowledge_mining.mining.infra.domain_pack import DomainProfile, RetrievalPolicy, get_default_profile

logger = logging.getLogger(__name__)

# Default retrieval policy — used when no profile is provided
_DEFAULT_POLICY = RetrievalPolicy()


# ---------------------------------------------------------------------------
# Question Generators
# ---------------------------------------------------------------------------

class LlmQuestionGenerator:
    """LLM-backed question generation via llm_service HTTP API.

    Batch async: submit_all -> poll_all -> return results.
    Results are capped at MAX_QUESTIONS_PER_SEGMENT.
    """

    def __init__(self, base_url: str = "http://localhost:8900", timeout: int = 120, profile: DomainProfile | None = None, knowledge_domain: str | None = None) -> None:
        from knowledge_mining.mining.infra.llm_client import LlmClient
        self._client = LlmClient(base_url=base_url)
        self._timeout = timeout
        self._last_task_ids: dict[str, str] = {}
        self._profile = profile
        self._knowledge_domain = knowledge_domain or (profile.domain_id if profile else None)

    def generate(self, segment: RawSegmentData) -> list[str]:
        """Single segment submit+poll (fallback)."""
        max_q = (self._profile or get_default_profile()).retrieval_policy.max_questions_per_segment
        try:
            task_id = self._client.submit_task(
                template_key="mining-question-gen",
                input={
                    "title": segment.section_title or "",
                    "content": segment.raw_text,
                },
                knowledge_domain=self._knowledge_domain,
                pipeline_stage="retrieval_units",
                expected_output_type="json_object",
            )
            if task_id is None:
                return []
            items = self._client.poll_result(task_id, timeout=self._timeout)
            if items is None:
                return []
            # Unwrap {"questions": [...]} wrapper (llm_client wraps dict into [dict])
            if items and isinstance(items[0], dict) and "questions" in items[0]:
                items = items[0]["questions"]
            # Providers may return either strings or one {"question": ...}
            # object per item. Normalize both shapes before applying the cap.
            questions = []
            for item in items:
                question = (
                    item.get("question")
                    if isinstance(item, dict)
                    else item
                )
                if isinstance(question, str) and question.strip():
                    questions.append(question)
            return questions[:max_q]
        except Exception:
            return []

    @property
    def last_task_ids(self) -> dict[str, str]:
        return dict(self._last_task_ids)

    def generate_batch(self, segments: list[RawSegmentData]) -> dict[str, list[str]]:
        """Batch: submit all tasks, then poll all results concurrently."""
        if not segments:
            return {}

        max_q = (self._profile or get_default_profile()).retrieval_policy.max_questions_per_segment

        # Phase 1: Submit all tasks
        seg_tasks: dict[str, str] = {}
        for seg in segments:
            seg_key = f"{seg.document_key}#{seg.segment_index}"
            task_id = self._client.submit_task(
                template_key="mining-question-gen",
                input={
                    "title": seg.section_title or "",
                    "content": seg.raw_text,
                },
                knowledge_domain=self._knowledge_domain,
                pipeline_stage="retrieval_units",
                expected_output_type="json_object",
            )
            if task_id:
                seg_tasks[seg_key] = task_id

        if not seg_tasks:
            return {}

        self._last_task_ids = dict(seg_tasks)

        # Phase 2: Poll all results concurrently
        raw_results = self._client.poll_all(seg_tasks)
        results: dict[str, list[str]] = {}
        for seg_key, items in raw_results.items():
            # Unwrap {"questions": [...]} wrapper (llm_client wraps dict into [dict])
            if items and isinstance(items[0], dict) and "questions" in items[0]:
                items = items[0]["questions"]
            questions = []
            for item in items:
                question = (
                    item.get("question")
                    if isinstance(item, dict)
                    else item
                )
                if isinstance(question, str) and question.strip():
                    questions.append(question)
            # Cap at max_questions_per_segment from profile policy
            if questions:
                results[seg_key] = questions[:max_q]

        return results


# ---------------------------------------------------------------------------
# Contextualizers
# ---------------------------------------------------------------------------

class LLMContextualizer:
    """Anthropic-style contextual retrieval via LLM.

    Generates brief context descriptions per segment.
    In v1.3, the context is folded into raw_text.search_text, NOT a separate unit.
    """

    def __init__(self, base_url: str = "http://localhost:8900", timeout: int = 120, knowledge_domain: str | None = None) -> None:
        from knowledge_mining.mining.infra.llm_client import LlmClient
        self._client = LlmClient(base_url=base_url)
        self._timeout = timeout
        self._last_task_ids: dict[str, str] = {}
        self._knowledge_domain = knowledge_domain

    @property
    def last_task_ids(self) -> dict[str, str]:
        return dict(self._last_task_ids)

    def contextualize(self, segments: list[RawSegmentData], document_text: str) -> dict[str, str]:
        """Generate context descriptions for all segments via LLM.

        Only contextualizes substantial segments (non-heading, >15 chars).
        Returns {segment_key: context_description}.
        """
        if not segments:
            return {}

        # Filter to substantial segments only — headings and tiny fragments
        # don't need LLM context (saves ~30% of LLM calls)
        substantial = [
            s for s in segments
            if s.block_type != "heading" and len(s.raw_text.strip()) > 15
        ]
        if not substantial:
            return {}

        seg_tasks: dict[str, str] = {}
        for seg in substantial:
            seg_key = f"{seg.document_key}#{seg.segment_index}"
            doc_preview = document_text[:2000] if len(document_text) > 2000 else document_text
            task_id = self._client.submit_task(
                template_key="mining-contextual-retrieval",
                input={
                    "document": doc_preview,
                    "segment": seg.raw_text[:500],
                },
                knowledge_domain=self._knowledge_domain,
                pipeline_stage="contextual_retrieval",
                expected_output_type="json_object",
            )
            if task_id:
                seg_tasks[seg_key] = task_id

        self._last_task_ids = dict(seg_tasks)

        raw_results = self._client.poll_all(seg_tasks)
        results: dict[str, str] = {}
        for seg_key, items in raw_results.items():
            if not items:
                continue
            item = items[0] if isinstance(items, list) else items
            context = item.get("context", "")
            if context:
                results[seg_key] = context

        return results


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_retrieval_units(
    segments: list[RawSegmentData],
    *,
    seg_ids: dict[str, str] | None = None,
    document_key: str = "",
    question_generator: QuestionGenerator | None = None,
    contextualizer: Contextualizer | None = None,
    profile: DomainProfile | None = None,
    raw_text_unit: bool | None = None,
    generated_question_unit: bool | None = None,
    table_row_unit: bool | None = None,
    max_questions_per_segment: int | None = None,
    min_questionworthy_tokens: int | None = None,
    apply_contextualizer: bool = True,
) -> list[RetrievalUnitData]:
    """Build retrieval units from segments.

    v1.3 strategy (2-2.5x density):
    1. raw_text: 1:1 with segment, search_text enriched with section + LLM context
    2. entity_card: only for strong entity types (from profile)
    3. table_row: per-row for table segments
    4. generated_question: 1-2 per substantial segment (from profile policy)

    Args:
        segments: Enriched segments to build units from.
        seg_ids: Map of segment_key -> segment UUID (from build_relations).
        document_key: Document key for unit naming.
        question_generator: Optional question generator (LLM-backed or NoOp).
        contextualizer: Optional contextualizer for search_text enrichment.
        profile: DomainProfile for entity types and retrieval policy.
    """
    if not segments:
        return []

    if profile is None:
        profile = get_default_profile()

    strong_types = profile.strong_entity_types
    policy = profile.retrieval_policy
    max_questions = (
        policy.max_questions_per_segment
        if max_questions_per_segment is None
        else max_questions_per_segment
    )
    max_entity_cards = policy.max_entity_cards_per_segment
    question_policy = (
        policy
        if min_questionworthy_tokens is None
        else replace(policy, min_questionworthy_tokens=min_questionworthy_tokens)
    )
    include_raw_text = True if raw_text_unit is None else raw_text_unit
    include_questions = (
        True if generated_question_unit is None else generated_question_unit
    )
    # 域包 retrieval_policy.table_row:"off" 是域级禁用意图，权威高于工作流算子参数。
    # 已发布工作流的 compiled_manifest 在编译期会把算子默认值冻进节点 params（如旧默认
    # tableRowUnit=true），改代码默认值改不到已发布 manifest。让域包 "off" 始终覆盖参数，
    # 域级策略才真正生效；非 "off" 时再按"参数优先、否则默认开"。
    if profile.retrieval_policy.table_row == "off":
        include_table_rows = False
    elif table_row_unit is None:
        include_table_rows = True
    else:
        include_table_rows = table_row_unit

    qgen = question_generator
    ctxer = contextualizer
    units: list[RetrievalUnitData] = []
    seen_entity_cards: set[str] = set()

    # Phase 1: Batch-generate all questions (submit all -> poll all)
    question_map: dict[str, list[str]] = {}
    qgen_task_ids: dict[str, str] = {}
    if qgen is not None and include_questions and max_questions > 0:
        questionworthy = [
            s for s in segments if _is_questionworthy(s, question_policy)
        ]
        raw_question_map = qgen.generate_batch(questionworthy)
        # v1.5: prune invalid questions from LLM output
        for seg_key, questions in raw_question_map.items():
            pruned = _prune_invalid_questions(questions)
            if pruned:
                question_map[seg_key] = pruned[:max_questions]
        if hasattr(qgen, "last_task_ids"):
            qgen_task_ids = qgen.last_task_ids

    # Phase 1b: Batch-generate contextual descriptions (for search_text enrichment)
    context_map: dict[str, str] = {}
    ctxer_task_ids: dict[str, str] = {}
    if (
        apply_contextualizer
        and ctxer is not None
        and profile.retrieval_policy.contextual_retrieval != "off"
    ):
        document_text = "\n".join(s.raw_text for s in segments)
        try:
            context_map = ctxer.contextualize(
                [s for s in segments if s.raw_text.strip()],
                document_text,
            )
            if hasattr(ctxer, "last_task_ids"):
                ctxer_task_ids = ctxer.last_task_ids
        except Exception as e:
            logger.warning("Contextualization failed: %s", e)
    elif not apply_contextualizer:
        for segment in segments:
            segment_key = f"{segment.document_key}#{segment.segment_index}"
            context, task_id = _context_from_segment(segment)
            if context:
                context_map[segment_key] = context
            if task_id:
                ctxer_task_ids[segment_key] = task_id

    # Phase 2: Build units for each segment
    for seg in segments:
        seg_key = f"{seg.document_key}#{seg.segment_index}"
        source_seg_id = (seg_ids or {}).get(seg_key)

        # Skip navigation segments (TOC, directory listings) — no retrieval value
        assessment = seg.metadata_json.get("content_assessment", {})
        if assessment.get("is_navigation"):
            continue

        # 1. raw_text unit — enriched with section context + optional LLM context
        llm_context = context_map.get(seg_key, "")
        ctx_task_id = ctxer_task_ids.get(seg_key)
        if include_raw_text:
            units.append(
                _make_raw_text_unit(seg, source_seg_id, llm_context, ctx_task_id)
            )

        # 2. entity_card units — only if policy enables it
        if profile.retrieval_policy.entity_card != "off":
            segment_card_count = 0
            for ref in seg.entity_refs_json:
                entity_type = ref.get("type", "")
                if entity_type not in strong_types:
                    continue
                # v1.5: Skip entity cards from navigation segments (LLM assessment)
                if assessment.get("is_navigation"):
                    continue
                entity_key = f"{entity_type}:{ref.get('name', '')}"
                if entity_key not in seen_entity_cards:
                    seen_entity_cards.add(entity_key)
                    units.append(_make_entity_card_unit(seg, ref, source_seg_id))
                    segment_card_count += 1
                    if segment_card_count >= max_entity_cards:
                        break

        # 3. table_row units — only if policy enables it
        if include_table_rows:
            units.extend(_make_table_row_units(seg, source_seg_id))

        # 4. generated_question units (capped by profile policy, already pruned)
        questions = question_map.get(seg_key, [])
        q_task_id = qgen_task_ids.get(seg_key)
        for qi, question in enumerate(questions):
            units.append(_make_generated_question_unit(
                seg, question, qi, source_seg_id, q_task_id,
            ))

    return units


def _context_from_segment(seg: RawSegmentData) -> tuple[str, str | None]:
    metadata = seg.metadata_json or {}
    return (
        str(metadata.get("context_description") or ""),
        metadata.get("context_task_id"),
    )


# ---------------------------------------------------------------------------
# Unit builders
# ---------------------------------------------------------------------------

def _make_raw_text_unit(
    seg: RawSegmentData,
    source_seg_id: str | None = None,
    llm_context: str = "",
    llm_task_id: str | None = None,
) -> RetrievalUnitData:
    """Primary retrieval unit: raw_text with enriched search_text.

    search_text composition (Anthropic contextual retrieval pattern):
    1. Section title context (if not already in raw_text)
    2. LLM-generated context (if available)
    3. Original raw_text
    All tokenized for FTS5 search.
    """
    # Build enriched search text
    search_parts: list[str] = []

    # Add structural breadcrumb from segment metadata (free context, no LLM)
    structural_context = seg.metadata_json.get("structural_context", "")
    if structural_context and structural_context not in seg.raw_text:
        search_parts.append(structural_context)
    else:
        # Fallback: add section titles not already in raw_text
        section_titles = [
            p.get("title", "") for p in seg.section_path if p.get("title")
        ]
        extra_titles = [t for t in section_titles if t and t not in seg.raw_text]
        if extra_titles:
            search_parts.append(" > ".join(extra_titles))

    # Add LLM-generated context (Anthropic pattern: brief context prepended)
    if llm_context:
        search_parts.append(llm_context)

    # Add original text
    search_parts.append(seg.raw_text)

    enriched_search = "\n".join(search_parts)

    # Build llm_result_refs_json for provenance
    llm_refs: dict[str, Any] = {}
    if llm_task_id:
        llm_refs = {
            "source": "contextual_retrieval",
            "task_id": llm_task_id,
        }

    # Build metadata
    metadata: dict[str, Any] = {"segment_index": seg.segment_index}
    if llm_context:
        metadata["context_description"] = llm_context

    return RetrievalUnitData(
        segment_key=f"{seg.document_key}#{seg.segment_index}",
        unit_key=f"ru:{seg.document_key}#{seg.segment_index}:raw_text",
        unit_type="raw_text",
        target_type="raw_segment",
        target_ref_json={
            "document_key": seg.document_key,
            "segment_index": seg.segment_index,
        },
        title=seg.section_title,
        text=seg.raw_text,
        search_text=tokenize_for_search(enriched_search),
        block_type=seg.block_type,
        semantic_role=seg.semantic_role,
        facets_json=_build_facets(seg),
        entity_refs_json=list(seg.entity_refs_json),
        source_refs_json=_build_source_refs(seg, source_seg_id),
        llm_result_refs_json=llm_refs,
        source_segment_id=source_seg_id,
        weight=1.0,
        metadata_json=metadata,
    )


def _make_entity_card_unit(
    seg: RawSegmentData,
    ref: dict[str, str],
    source_seg_id: str | None = None,
) -> RetrievalUnitData:
    """Entity card: only for strong entity types (command/protocol/network_element/parameter)."""
    entity_type = ref.get("type", "unknown")
    entity_name = ref.get("name", "unknown")

    description = _extract_entity_context(entity_name, seg.raw_text)
    text = f"{entity_name}（{entity_type}）"
    if description:
        text += f" {description}"
    elif seg.section_title:
        text += f" — 见 {seg.section_title}"

    return RetrievalUnitData(
        segment_key=f"{seg.document_key}#{seg.segment_index}",
        unit_key=f"ru:entity:{entity_type}:{entity_name}",
        unit_type="entity_card",
        target_type="entity",
        target_ref_json={"entity_type": entity_type, "entity_name": entity_name},
        title=entity_name,
        text=text,
        search_text=tokenize_for_search(f"{entity_name} {entity_type} {description}"),
        block_type=seg.block_type,
        semantic_role=seg.semantic_role,
        facets_json={"entity_type": entity_type},
        entity_refs_json=[ref],
        source_refs_json=_build_source_refs(seg, source_seg_id),
        source_segment_id=source_seg_id,
        weight=0.5,
        metadata_json={"first_seen_in": seg.document_key},
    )


def _make_generated_question_unit(
    seg: RawSegmentData,
    question: str,
    question_index: int,
    source_seg_id: str | None = None,
    llm_task_id: str | None = None,
) -> RetrievalUnitData:
    """Generated question unit: one per LLM-generated question (max 2 per segment)."""
    # v1.5: No Qn prefix — pure question text as title
    title = question[:120]
    text = f"{question}\n---\n来源: {seg.section_title or '未知'}\n{seg.raw_text[:200]}"
    search_text = tokenize_for_search(f"{question} {seg.section_title or ''}")
    llm_refs: dict[str, Any] = {"source": "llm_runtime", "question_index": question_index}
    if llm_task_id:
        llm_refs["task_id"] = llm_task_id

    return RetrievalUnitData(
        segment_key=f"{seg.document_key}#{seg.segment_index}",
        unit_key=f"ru:{seg.document_key}#{seg.segment_index}:gen_q_{question_index}",
        unit_type="generated_question",
        target_type="raw_segment",
        target_ref_json={
            "document_key": seg.document_key,
            "segment_index": seg.segment_index,
            "question_index": question_index,
        },
        title=title,
        text=text,
        search_text=search_text,
        block_type=seg.block_type,
        semantic_role=seg.semantic_role,
        facets_json=_build_facets(seg),
        entity_refs_json=list(seg.entity_refs_json),
        source_refs_json=_build_source_refs(seg, source_seg_id),
        llm_result_refs_json=llm_refs,
        source_segment_id=source_seg_id,
        weight=0.7,
        metadata_json={"question_index": question_index},
    )


def _make_table_row_units(
    seg: RawSegmentData, source_seg_id: str | None = None,
) -> list[RetrievalUnitData]:
    """Generate per-row retrieval units for table segments."""
    if seg.block_type != "table":
        return []

    struct = seg.structure_json
    columns = struct.get("columns", [])
    rows = struct.get("rows", [])
    if not columns or not rows:
        return []

    units: list[RetrievalUnitData] = []
    for row_idx, row in enumerate(rows):
        parts = []
        all_values = []
        for col in columns:
            val = row.get(col, "")
            if val:
                parts.append(f"{col}为{val}")
                all_values.append(val)

        if not parts:
            continue

        text = "，".join(parts) + "。"
        search_text = tokenize_for_search(" ".join(all_values + columns))
        seg_key = f"{seg.document_key}#{seg.segment_index}"

        units.append(RetrievalUnitData(
            segment_key=seg_key,
            unit_key=f"ru:{seg.document_key}#{seg.segment_index}:table_row_{row_idx}",
            unit_type="table_row",
            target_type="raw_segment",
            target_ref_json={
                "document_key": seg.document_key,
                "segment_index": seg.segment_index,
                "row_index": row_idx,
            },
            title=f"行{row_idx + 1}: {'、'.join(all_values[:3])}",
            text=text,
            search_text=search_text,
            block_type=seg.block_type,
            semantic_role=seg.semantic_role,
            facets_json=_build_facets(seg),
            entity_refs_json=list(seg.entity_refs_json),
            source_refs_json=_build_source_refs(seg, source_seg_id),
            source_segment_id=source_seg_id,
            weight=0.8,
            metadata_json={"row_index": row_idx, "columns": columns},
        ))

    return units


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_facets(seg: RawSegmentData) -> dict[str, Any]:
    """Build facets from segment metadata."""
    facets: dict[str, Any] = {}
    if seg.block_type:
        facets["block_type"] = seg.block_type
    if seg.semantic_role:
        facets["semantic_role"] = seg.semantic_role
    if seg.section_path:
        facets["section_depth"] = len(seg.section_path)
    return facets


def _build_source_refs(seg: RawSegmentData, source_seg_id: str | None = None) -> dict[str, Any]:
    """Build source_refs for provenance tracing."""
    refs: dict[str, Any] = {
        "document_key": seg.document_key,
        "segment_index": seg.segment_index,
        "raw_segment_ids": [source_seg_id] if source_seg_id else [],
    }
    if seg.source_offsets_json:
        refs["offsets"] = seg.source_offsets_json
    return refs


def _is_questionworthy(seg: RawSegmentData, policy: RetrievalPolicy = _DEFAULT_POLICY) -> bool:
    """Minimal universal gate — configurable via RetrievalPolicy.

    Only skips segments that are not worth sending to LLM.
    All content quality decisions are made BY the LLM, not by rules.
    """
    # Universal: headings are structural, not content
    if seg.block_type == "heading":
        return False
    # Configurable: too short to contain meaningful information
    if seg.token_count is not None and seg.token_count < policy.min_questionworthy_tokens:
        return False
    if len(seg.raw_text.strip()) < 15:
        return False
    # v1.5: If enrichment already assessed this segment as non-substantive, skip LLM call
    assessment = seg.metadata_json.get("content_assessment", {})
    if assessment and not assessment.get("is_substantive", True):
        return False
    # Configurable: exclude non-questionworthy roles; "unknown" passes through
    if seg.semantic_role in policy.not_questionworthy_roles:
        return False
    return True


def _prune_invalid_questions(questions: list[str]) -> list[str]:
    """Prune invalid questions from LLM output (LlamaIndex schema validation pattern).

    Validates structure only, not content. The LLM already made content quality decisions.
    """
    valid: list[str] = []
    for q in questions:
        q = q.strip()
        # Structural validation (not content quality — LLM already decided)
        if len(q) < 5:
            continue
        # Strip Qn prefix if LLM added it (e.g. "Q1: How to...")
        if q.startswith("Q") and len(q) > 1 and q[1].isdigit():
            q = re.sub(r"^Q\d+\s*[:：]\s*", "", q)
        if not q or len(q) < 5:
            continue
        valid.append(q)
    return valid


def _extract_entity_context(name: str, raw_text: str, window: int = 80) -> str:
    """Extract text around entity mention for context."""
    idx = raw_text.find(name)
    if idx < 0:
        return ""
    start = max(0, idx - window // 2)
    end = min(len(raw_text), idx + len(name) + window // 2)
    ctx = raw_text[start:end].strip()
    if start > 0:
        ctx = "..." + ctx
    if end < len(raw_text):
        ctx = ctx + "..."
    return ctx
