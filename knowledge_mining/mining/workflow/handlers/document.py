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




# ---------------------------------------------------------------------------
# M6 新算子（SRS §10.2）：解析与切片分离，经注入的服务组件走新链
# ---------------------------------------------------------------------------


class ParsedViaNewChain:
    """document_parse 的产出上下文：新链解析结果指针（快照/IR）.

    冻结 dataclass 风格的轻量容器（与 DocumentContext 同 immutable 约定）。
    """

    __slots__ = (
        "run_id", "snapshot_id", "parse_ir_storage_object_id",
        "parser_fingerprint", "quality_status", "raw_file",
    )

    def __init__(
        self,
        *,
        run_id: str,
        snapshot_id: str | None,
        parse_ir_storage_object_id: str | None,
        parser_fingerprint: str | None,
        quality_status: str | None,
        raw_file: Any,
    ) -> None:
        self.run_id = run_id
        self.snapshot_id = snapshot_id
        self.parse_ir_storage_object_id = parse_ir_storage_object_id
        self.parser_fingerprint = parser_fingerprint
        self.quality_status = quality_status
        self.raw_file = raw_file


def document_parse_handler(
    state: DocumentState, params: Mapping[str, Any], runtime: Any
) -> OperatorResult:
    """冻结输入 → 新链解析（质量门控 + 快照转正）→ 解析指针上下文.

    服务组件（``runtime.services.document_parse_service``，同步门面）由
    组合根注入；未接线时显式 FAILED——v2 骨架下不静默回落旧解析，
    保证「任何知识线都有解析事实与快照」的一致性（SRS §10.2）。
    """
    from ..operators.options import DocumentParseOptions

    options = DocumentParseOptions.model_validate(dict(params))
    ctx = state.context
    raw_file = getattr(ctx, "raw_file", None)
    if raw_file is None:
        return OperatorResult(state, frozenset(), OperatorStatus.SKIPPED)
    service = getattr(runtime.services, "document_parse_service", None)
    if service is None:
        return OperatorResult(
            state, frozenset(), OperatorStatus.FAILED,
            error_code="document_parse_unavailable",
            error_message=(
                "document_parse service is not configured on this runtime "
                "(new-chain parse requires the M4 operator stack)"
            ),
        )
    try:
        outcome = service.parse_document(
            raw_file,
            params=options.model_dump(by_alias=True),
            domain=getattr(runtime.services, "domain", None) or "default",
            run_document_id=state.run_document_id,
        )
    except Exception as exc:  # noqa: BLE001
        return _on_error(
            state, code="document_parse_failed", exc=exc,
            mode=OperatorStatus.FAILED,
        )
    if outcome is None:
        return OperatorResult(state, frozenset(), OperatorStatus.SKIPPED)
    parsed = ParsedViaNewChain(
        run_id=getattr(outcome, "run_id", None) or outcome.id,
        snapshot_id=getattr(outcome, "snapshot_id", None),
        parse_ir_storage_object_id=getattr(
            outcome, "parse_ir_storage_object_id", None
        ),
        parser_fingerprint=getattr(outcome, "parser_fingerprint", None),
        quality_status=getattr(outcome, "quality_status", None),
        raw_file=raw_file,
    )
    return _success(state, parsed, "parsed_documents")


def segment_compile_handler(
    state: DocumentState, params: Mapping[str, Any], runtime: Any
) -> OperatorResult:
    """快照 IR + 参数档位 → 切片 → 兼容投影进 DocumentContext.segments.

    下游（enrich/检索单元/向量化）消费的 ``ctx.segments`` 形状不变，
    零改动复用；元素映射与表格行细节保留在 source_offsets_json /
    structure_json（§4.12/§2.3 兼容不变量）。
    """
    from ..operators.options import SegmentCompileOptions

    options = SegmentCompileOptions.model_validate(dict(params))
    parsed = state.context
    if not isinstance(parsed, ParsedViaNewChain):
        return OperatorResult(
            state, frozenset(), OperatorStatus.FAILED,
            error_code="segment_compile_bad_input",
            error_message=(
                "segment_compile requires upstream document_parse output "
                f"(got {type(parsed).__name__})"
            ),
        )
    service = getattr(runtime.services, "segment_compile_service", None)
    if service is None:
        return OperatorResult(
            state, frozenset(), OperatorStatus.FAILED,
            error_code="segment_compile_unavailable",
            error_message=(
                "segment_compile service is not configured on this runtime"
            ),
        )
    try:
        compiled = service.compile_for_snapshot(
            snapshot_id=parsed.snapshot_id,
            parse_ir_storage_object_id=parsed.parse_ir_storage_object_id,
            params=options.model_dump(by_alias=True),
        )
    except Exception as exc:  # noqa: BLE001
        return _on_error(
            state, code="segment_compile_failed", exc=exc,
            mode=OperatorStatus.FAILED,
        )
    segments = getattr(compiled, "segments", ()) or ()
    if not segments:
        return OperatorResult(state, frozenset(), OperatorStatus.SKIPPED)

    from knowledge_mining.mining.segment_compiler.projection import (
        to_raw_segment_data,
    )
    from knowledge_mining.mining.pipeline import DocumentContext  # noqa: F401

    document_key = getattr(
        getattr(parsed.raw_file, "document_key", None), "document_key", None
    ) or getattr(parsed.raw_file, "document_key", None) or state.doc_key
    projected = tuple(
        to_raw_segment_data(seg, document_key=document_key)
        for seg in segments
    )
    seg_ids = {
        seg.segment_index: f"{document_key}#{seg.segment_index}"
        for seg in segments
    }
    ctx = DocumentContext(
        raw_file=parsed.raw_file,
        segments=projected,
        seg_ids=seg_ids,
        run_document_id=state.run_document_id,
    )
    return _success(state, ctx, "parsed_segments")

DOCUMENT_HANDLERS = {
    "parse_segment": parse_segment_handler,
    "document_parse": document_parse_handler,
    "segment_compile": segment_compile_handler,
    "enrich": enrich_handler,
    "discourse_line": discourse_line_handler,
    "contextual_retrieval_enrich": contextual_retrieval_enrich_handler,
    "retrieval_unit_build": retrieval_unit_build_handler,
    "embedding": embedding_handler,
    "entity_extract": entity_extract_handler,
    "entity_resolve": entity_resolve_handler,
    "entity_relation_extract": entity_relation_extract_handler,
}
