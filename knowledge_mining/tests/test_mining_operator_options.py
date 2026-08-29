from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from knowledge_mining.mining.contracts.models import (
    DocumentProfile,
    RawSegmentData,
    RetrievalUnitData,
    SegmentRelationData,
)
from knowledge_mining.mining.infra.domain_pack import RetrievalPolicy
from knowledge_mining.mining.infra.ontology_store import UNTYPED_NODE_TYPE
from knowledge_mining.mining.pipeline import (
    DocumentContext,
    PipelineConfig,
    contextual_retrieval_stage,
    discourse_stage,
    embedding_stage,
    enrich_stage,
    resolve_stage,
    retrieval_units_stage,
    segment_stage,
)
from knowledge_mining.mining.stages.entity_extract import _apply_entity_result
from knowledge_mining.mining.workflow.operators.options import (
    DiscourseOptions,
    EmbeddingOptions,
    EnrichOptions,
    EntityResolveOptions,
    ParseSegmentOptions,
    RetrievalUnitOptions,
)


def _segment(
    index: int = 0,
    *,
    text: str = "AMF context and network behavior are described in detail.",
    token_count: int = 20,
    block_type: str = "paragraph",
    metadata: dict | None = None,
    refs: list[dict] | None = None,
    structure: dict | None = None,
) -> RawSegmentData:
    return RawSegmentData(
        document_key="doc:/a",
        segment_index=index,
        block_type=block_type,
        raw_text=text,
        normalized_text=text,
        token_count=token_count,
        metadata_json=metadata or {},
        entity_refs_json=refs or [],
        structure_json=structure or {},
    )


def _profile() -> SimpleNamespace:
    return SimpleNamespace(
        strong_entity_types=frozenset(),
        retrieval_policy=RetrievalPolicy(),
    )


def _ctx(*segments: RawSegmentData) -> DocumentContext:
    return DocumentContext(
        profile=DocumentProfile(document_key="doc:/a"),
        segments=tuple(segments),
        seg_ids={f"doc:/a#{item.segment_index}": f"seg-{item.segment_index}" for item in segments},
    )


def test_parse_options_reach_segmenter_as_immutable_policy_override() -> None:
    seen = []

    class Segmenter:
        def segment(self, tree, profile, **kwargs):
            seen.append(kwargs["policy_override"])
            return [_segment()]

    cfg = PipelineConfig(domain="odn", segmenter=Segmenter())
    ctx = DocumentContext(
        tree=object(), profile=DocumentProfile(document_key="doc:/a")
    )
    options = ParseSegmentOptions(
        minSegmentTokens=12,
        maxSegmentTokens=64,
        mergeSmallSegments=False,
        absorbChildOrphans=False,
        mergeLeadIntoChild=False,
        structuralContextMode="off",
    )

    result = segment_stage(ctx, cfg, options=options)

    assert len(result.segments) == 1
    assert dataclasses.is_dataclass(seen[0])
    assert seen[0].min_segment_tokens == 12
    assert seen[0].max_segment_tokens == 64
    assert seen[0].merge_small_segments is False
    assert seen[0].structural_context_mode == "off"


def test_enrich_options_skip_small_segments_and_preserve_original_order() -> None:
    calls = []

    class Enricher:
        def enrich_batch(self, segments):
            calls.append([item.segment_index for item in segments])
            return [dataclasses.replace(item, semantic_role="concept") for item in segments]

    small = _segment(0, token_count=5)
    substantial = _segment(1, token_count=20)
    result = enrich_stage(
        _ctx(small, substantial),
        PipelineConfig(domain="odn", enricher=Enricher()),
        options=EnrichOptions(minEnrichTokens=10),
    )

    assert calls == [[1]]
    assert result.segments[0] is small
    assert result.segments[1].semantic_role == "concept"


def test_discourse_options_override_window_and_confidence_at_builder_boundary() -> None:
    seen = []

    class Builder:
        def build(self, segments, **kwargs):
            seen.append(kwargs)
            candidates = [
                SegmentRelationData("doc:/a#0", "doc:/a#1", "elaborates", confidence=0.6),
                SegmentRelationData("doc:/a#1", "doc:/a#2", "contrast", confidence=0.9),
            ]
            return [
                item for item in candidates
                if item.confidence >= kwargs["min_confidence"]
            ]

    result = discourse_stage(
        _ctx(_segment(0), _segment(1), _segment(2)),
        PipelineConfig(domain="odn", discourse_relation_builder=Builder()),
        options=DiscourseOptions(windowSize=3, minConfidence=0.8),
    )

    assert seen == [{
        "seg_ids": {"doc:/a#0": "seg-0", "doc:/a#1": "seg-1", "doc:/a#2": "seg-2"},
        "window_size": 3,
        "min_confidence": 0.8,
    }]
    assert [item.relation_type for item in result.relations] == ["contrast"]


def test_retrieval_options_disable_generators_and_filter_unit_types() -> None:
    class Questions:
        def generate_batch(self, segments):
            raise AssertionError("disabled question generation must not run")

    table = _segment(
        0,
        block_type="table",
        structure={"columns": ["name"], "rows": [{"name": "AMF"}]},
    )
    cfg = PipelineConfig(
        domain="odn", question_generator=Questions(), domain_profile=_profile()
    )

    result = retrieval_units_stage(
        _ctx(table),
        cfg,
        options=RetrievalUnitOptions(
            rawTextUnit=True,
            generatedQuestionUnit=False,
            tableRowUnit=False,
            maxQuestionsPerSegment=0,
            minQuestionworthyTokens=50,
        ),
    )

    assert {item.unit_type for item in result.retrieval_units} == {"raw_text"}


def test_retrieval_options_cap_questions_before_unit_creation() -> None:
    class Questions:
        last_task_ids = {"doc:/a#0": "question-task"}

        def generate_batch(self, segments):
            assert [item.segment_index for item in segments] == [0]
            return {"doc:/a#0": ["What is AMF?", "How does AMF work?"]}

    cfg = PipelineConfig(
        domain="odn", question_generator=Questions(), domain_profile=_profile()
    )
    result = retrieval_units_stage(
        _ctx(_segment(token_count=60)),
        cfg,
        options=RetrievalUnitOptions(
            rawTextUnit=False,
            generatedQuestionUnit=True,
            tableRowUnit=False,
            maxQuestionsPerSegment=1,
            minQuestionworthyTokens=10,
        ),
    )

    assert [item.unit_type for item in result.retrieval_units] == [
        "generated_question"
    ]


def test_contextual_enrichment_runs_once_and_builder_reuses_metadata() -> None:
    class Contextualizer:
        calls = 0
        last_task_ids = {"doc:/a#0": "context-task"}

        def contextualize(self, segments, document_text):
            self.calls += 1
            assert "network behavior" in document_text
            return {"doc:/a#0": "AMF contextual description"}

    contextualizer = Contextualizer()
    cfg = PipelineConfig(
        domain="odn", contextualizer=contextualizer, domain_profile=_profile()
    )

    enriched = contextual_retrieval_stage(_ctx(_segment()), cfg)
    built = retrieval_units_stage(
        enriched, cfg, options=RetrievalUnitOptions(
            generatedQuestionUnit=False, tableRowUnit=False
        )
    )

    assert contextualizer.calls == 1
    assert enriched.segments[0].metadata_json["context_task_id"] == "context-task"
    assert "amf" in built.retrieval_units[0].search_text.lower()
    assert built.retrieval_units[0].llm_result_refs_json["task_id"] == "context-task"


def test_entity_threshold_schema_escape_and_limit_are_applied_deterministically() -> None:
    result = {
        "entities": [
            {"name": "B", "type": "network_element", "confidence": 0.7},
            {"name": "A", "type": "network_element", "confidence": 0.9},
            {"name": "low", "type": "network_element", "confidence": 0.4},
            {"name": "off", "type": "vendor_type", "confidence": 0.95},
        ]
    }

    strict = _apply_entity_result(
        _segment(),
        result,
        frozenset({"network_element"}),
        min_confidence=0.5,
        allow_out_of_schema=False,
        max_entities=1,
    )
    escaped = _apply_entity_result(
        _segment(),
        result,
        frozenset({"network_element"}),
        min_confidence=0.5,
        allow_out_of_schema=True,
        max_entities=2,
    )

    assert [item["name"] for item in strict.entity_refs_json] == ["A"]
    assert [item["name"] for item in escaped.entity_refs_json] == ["off", "A"]
    assert escaped.entity_refs_json[0]["type"] == UNTYPED_NODE_TYPE


def test_disabling_alias_resolution_marks_mentions_pending_without_lookup() -> None:
    class Resolver:
        def resolve_batch(self, segments):
            raise AssertionError("alias resolver must not run")

    source = _segment(refs=[{
        "type": "network_element",
        "name": "AMF",
        "canonical_name": "Access and Mobility Management Function",
        "resolve_status": "auto",
    }])
    result = resolve_stage(
        _ctx(source),
        PipelineConfig(domain="odn", resolver=Resolver()),
        options=EntityResolveOptions(autoResolveAliases=False),
    )

    ref = result.segments[0].entity_refs_json[0]
    assert ref["canonical_name"] is None
    assert ref["resolve_status"] == "pending"
