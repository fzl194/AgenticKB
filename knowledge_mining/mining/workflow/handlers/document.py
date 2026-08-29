from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from knowledge_mining.mining.pipeline import (
    embedding_stage,
    parse_stage,
    segment_stage,
)

from ..core import (
    DocumentState,
    OperatorResult,
    OperatorStatus,
    OperatorWarning,
)
from ..operators.options import (
    EmbeddingOptions,
    ParseSegmentOptions,
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




# ---------------------------------------------------------------------------
# M6 新算子（SRS §10.2）：解析与切片分离，经注入的服务组件走新链
# ---------------------------------------------------------------------------


def document_parse_handler(
    state: DocumentState, params: Mapping[str, Any], runtime: Any
) -> OperatorResult:
    """冻结输入 → 新链解析（质量门控 + 快照转正）→ 版本化 bundle.

    批次8 M1（24 号 §5.2）：直接产出 ``MiningDocumentBundle``（快照/IR
    指针 + 文档生命周期事实），不做 legacy DocumentContext 投影。
    服务组件（``runtime.services.document_parse_service``，同步门面）由
    组合根注入；未接线时显式 FAILED——不静默回落旧解析。
    """
    from ..bundle import MiningDocumentBundle
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
    bundle = MiningDocumentBundle(
        document_ref=state.doc_key,
        run_document_id=state.run_document_id,
        snapshot_ref=getattr(outcome, "snapshot_id", None),
        parse_ir_ref=getattr(outcome, "parse_ir_storage_object_id", None),
        parser_fingerprint=getattr(outcome, "parser_fingerprint", None),
        quality_status=getattr(outcome, "quality_status", None),
        raw_file=raw_file,
        profile=getattr(ctx, "profile", None),
        action=getattr(ctx, "action", None),
        existing_doc=getattr(ctx, "existing_doc", None),
        document_id=getattr(ctx, "document_id", None),
    )
    return _success(state, bundle, "parsed_documents")


def segment_compile_handler(
    state: DocumentState, params: Mapping[str, Any], runtime: Any
) -> OperatorResult:
    """快照 IR + 参数档位 → 切片 → 续写 bundle（编译计数 + 文档事实）.

    批次8 M1（24 号 §5.3）：切片本体由 SegmentStore 持有（按
    snapshot_ref 读），bundle 只携带计数与确定性统计——**兼容投影
    （to_raw_segment_data → DocumentContext.segments）已删除**。
    """
    from ..bundle import MiningDocumentBundle, compute_document_facts
    from ..operators.options import SegmentCompileOptions

    options = SegmentCompileOptions.model_validate(dict(params))
    bundle = state.context
    if not isinstance(bundle, MiningDocumentBundle):
        return OperatorResult(
            state, frozenset(), OperatorStatus.FAILED,
            error_code="segment_compile_bad_input",
            error_message=(
                "segment_compile requires upstream document_parse bundle "
                f"(got {type(bundle).__name__})"
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
            snapshot_id=bundle.snapshot_ref,
            parse_ir_storage_object_id=bundle.parse_ir_ref,
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

    updated = bundle.with_updates(
        compiled_segment_count=len(segments),
        compiler_fingerprint=getattr(compiled, "compiler_fingerprint", None),
        document_facts=compute_document_facts(segments),
    )
    return _success(state, updated, "parsed_segments")


def retrieval_unit_project_handler(
    state: DocumentState, params: Mapping[str, Any], runtime: Any
) -> OperatorResult:
    """结构事实 → 类型化搜索表示 → bundle（计数）+ 暂存写入.

    批次8 M2（24 号 §5.4）：纯投影（无 LLM、无资产入库）；表示本体写
    RepresentationStore 暂存（asset_persist/M5 正式入库），bundle 只带
    representations_count 与 capability 事实。
    """
    from ..bundle import MiningDocumentBundle
    from ..operators.options import RetrieProjectOptions

    options = RetrieProjectOptions.model_validate(dict(params))
    bundle = state.context
    if not isinstance(bundle, MiningDocumentBundle):
        return OperatorResult(
            state, frozenset(), OperatorStatus.FAILED,
            error_code="retrieval_unit_project_bad_input",
            error_message=(
                "retrieval_unit_project requires upstream document_parse bundle "
                f"(got {type(bundle).__name__})"
            ),
        )
    service = getattr(runtime.services, "retrieval_project_service", None)
    if service is None:
        return OperatorResult(
            state, frozenset(), OperatorStatus.FAILED,
            error_code="retrieval_unit_project_unavailable",
            error_message=(
                "retrieval_project service is not configured on this runtime"
            ),
        )
    try:
        projected = service.project_for_snapshot(
            snapshot_id=bundle.snapshot_ref,
            document_ref=bundle.document_ref,
            params=options.model_dump(by_alias=True),
        )
    except Exception as exc:  # noqa: BLE001
        return _on_error(
            state, code="retrieval_unit_project_failed", exc=exc,
            mode=OperatorStatus.FAILED,
        )
    representations = getattr(projected, "representations", ()) or ()
    if not representations:
        return OperatorResult(state, frozenset(), OperatorStatus.SKIPPED)

    updated = bundle.with_updates(
        representations_count=len(representations),
        capability_facts=bundle.capability_facts | frozenset({"retrieval_units"}),
    )
    return _success(state, updated, "retrieval_units")


# 批次8 M0：退役算子（enrich/discourse_line/contextual_retrieval_enrich/
# retrieval_unit_build）与实体研究算子的 handler 已移除/隔离（research.py）。
DOCUMENT_HANDLERS = {
    "parse_segment": parse_segment_handler,
    "document_parse": document_parse_handler,
    "segment_compile": segment_compile_handler,
    "retrieval_unit_project": retrieval_unit_project_handler,
    "embedding": embedding_handler,
}
