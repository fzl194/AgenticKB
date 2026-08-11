from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from knowledge_mining.mining.pipeline import (
    contextual_retrieval_stage,
    discourse_stage,
    embedding_stage,
    enrich_stage,
    entity_extract_stage,
    entity_relations_stage,
    parse_stage,
    resolve_stage,
    retrieval_units_stage,
    segment_stage,
)

from ..core import (
    DocumentState,
    OperatorResult,
    OperatorStatus,
    OperatorWarning,
)
from ..operators.options import (
    DiscourseOptions,
    EmbeddingOptions,
    EmptyOptions,
    EnrichOptions,
    EntityExtractOptions,
    EntityRelationOptions,
    EntityResolveOptions,
    ParseSegmentOptions,
    RetrievalUnitOptions,
)


def _pipeline_config(runtime: Any) -> Any:
    config = getattr(runtime.services, "pipeline_config", None)
    if config is None:
        raise RuntimeError("Handler runtime has no PipelineConfig")
    return config


def _not_applicable(state: DocumentState) -> OperatorResult:
    capability = frozenset({"ontology_not_applicable"})
    return OperatorResult(
        state.with_context(state.context, capabilities=capability),
        capability,
        OperatorStatus.NOT_APPLICABLE,
    )


def _success(
    state: DocumentState, context: Any, capability: str
) -> OperatorResult:
    provided = frozenset({capability})
    return OperatorResult(
        state.with_context(context, capabilities=provided),
        provided,
        OperatorStatus.SUCCESS,
    )


def _on_error(
    state: DocumentState,
    *,
    code: str,
    exc: Exception,
    mode: OperatorStatus,
    capability: str | None = None,
) -> OperatorResult:
    provided = frozenset({capability}) if capability else frozenset()
    output = (
        state.with_context(state.context, capabilities=provided)
        if mode in {OperatorStatus.FALLBACK, OperatorStatus.SKIPPED}
        else state
    )
    return OperatorResult(
        output,
        provided,
        mode,
        warnings=(OperatorWarning(code, str(exc)),),
        error_code=(code if mode is OperatorStatus.FAILED else None),
        error_message=(str(exc) if mode is OperatorStatus.FAILED else None),
    )


def parse_segment_handler(
    state: DocumentState, params: Mapping[str, Any], runtime: Any
) -> OperatorResult:
    options = ParseSegmentOptions.model_validate(dict(params))
    try:
        cfg = _pipeline_config(runtime)
        parsed = parse_stage(state.context, cfg, options=options)
        if parsed.tree is None:
            return OperatorResult(
                state.with_context(parsed), frozenset(), OperatorStatus.SKIPPED
            )
        segmented = segment_stage(parsed, cfg, options=options)
        if not segmented.segments:
            return OperatorResult(
                state.with_context(segmented), frozenset(), OperatorStatus.SKIPPED
            )
        return _success(state, segmented, "parsed_segments")
    except Exception as exc:
        return _on_error(
            state,
            code="parse_segment_failed",
            exc=exc,
            mode=OperatorStatus.FAILED,
        )


def _document_handler(
    state: DocumentState,
    params: Mapping[str, Any],
    runtime: Any,
    *,
    options_type: Any,
    stage: Callable[..., Any],
    capability: str,
    error_status: OperatorStatus,
    error_code: str,
    ontology_required: bool = False,
) -> OperatorResult:
    if ontology_required and runtime.ontology_version_id is None:
        return _not_applicable(state)
    options = options_type.model_validate(dict(params))
    try:
        context = stage(
            state.context, _pipeline_config(runtime), options=options
        )
    except Exception as exc:
        return _on_error(
            state,
            code=error_code,
            exc=exc,
            mode=error_status,
            capability=(
                capability
                if error_status in {OperatorStatus.FALLBACK, OperatorStatus.SKIPPED}
                else None
            ),
        )
    return _success(state, context, capability)


def enrich_handler(state, params, runtime) -> OperatorResult:
    return _document_handler(
        state,
        params,
        runtime,
        options_type=EnrichOptions,
        stage=enrich_stage,
        capability="semantic_enrichment",
        error_status=OperatorStatus.FALLBACK,
        error_code="enrich_fallback",
    )


def discourse_line_handler(state, params, runtime) -> OperatorResult:
    return _document_handler(
        state,
        params,
        runtime,
        options_type=DiscourseOptions,
        stage=discourse_stage,
        capability="discourse_relations",
        error_status=OperatorStatus.SKIPPED,
        error_code="discourse_empty_fallback",
    )


def contextual_retrieval_enrich_handler(state, params, runtime) -> OperatorResult:
    return _document_handler(
        state,
        params,
        runtime,
        options_type=EmptyOptions,
        stage=contextual_retrieval_stage,
        capability="retrieval_context",
        error_status=OperatorStatus.FALLBACK,
        error_code="contextual_retrieval_fallback",
    )


def retrieval_unit_build_handler(state, params, runtime) -> OperatorResult:
    return _document_handler(
        state,
        params,
        runtime,
        options_type=RetrievalUnitOptions,
        stage=retrieval_units_stage,
        capability="retrieval_units",
        error_status=OperatorStatus.FAILED,
        error_code="retrieval_unit_build_failed",
    )


def embedding_handler(state, params, runtime) -> OperatorResult:
    options = EmbeddingOptions.model_validate(dict(params))
    result = _document_handler(
        state,
        params,
        runtime,
        options_type=EmbeddingOptions,
        stage=embedding_stage,
        capability="embeddings",
        error_status=OperatorStatus.FALLBACK,
        error_code="embedding_fallback",
    )
    selected = [
        item
        for item in state.context.retrieval_units
        if item.text and item.unit_type in options.unit_types
    ]
    if (
        result.status is OperatorStatus.SUCCESS
        and selected
        and not result.outputs.context.embeddings
    ):
        return _on_error(
            state,
            code="embedding_fallback",
            exc=RuntimeError("Embedding service returned no vectors"),
            mode=OperatorStatus.FALLBACK,
            capability="embeddings",
        )
    return result


def entity_extract_handler(state, params, runtime) -> OperatorResult:
    return _document_handler(
        state,
        params,
        runtime,
        options_type=EntityExtractOptions,
        stage=entity_extract_stage,
        capability="entity_mentions",
        error_status=OperatorStatus.SKIPPED,
        error_code="entity_extract_empty_fallback",
        ontology_required=True,
    )


def entity_resolve_handler(state, params, runtime) -> OperatorResult:
    return _document_handler(
        state,
        params,
        runtime,
        options_type=EntityResolveOptions,
        stage=resolve_stage,
        capability="resolved_entities",
        error_status=OperatorStatus.SKIPPED,
        error_code="entity_resolve_empty_fallback",
        ontology_required=True,
    )


def entity_relation_extract_handler(state, params, runtime) -> OperatorResult:
    return _document_handler(
        state,
        params,
        runtime,
        options_type=EntityRelationOptions,
        stage=entity_relations_stage,
        capability="entity_relations",
        error_status=OperatorStatus.SKIPPED,
        error_code="entity_relation_empty_fallback",
        ontology_required=True,
    )


DOCUMENT_HANDLERS = {
    "parse_segment": parse_segment_handler,
    "enrich": enrich_handler,
    "discourse_line": discourse_line_handler,
    "contextual_retrieval_enrich": contextual_retrieval_enrich_handler,
    "retrieval_unit_build": retrieval_unit_build_handler,
    "embedding": embedding_handler,
    "entity_extract": entity_extract_handler,
    "entity_resolve": entity_resolve_handler,
    "entity_relation_extract": entity_relation_extract_handler,
}
