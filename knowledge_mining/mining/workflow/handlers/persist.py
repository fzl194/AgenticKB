from __future__ import annotations

from contextlib import contextmanager
from threading import Lock, RLock
from typing import Any, Mapping

from ..core import DocumentState, OperatorResult, OperatorStatus, OperatorWarning


_LOCKS_GUARD = Lock()
_RUN_LOCKS: dict[str, RLock] = {}


@contextmanager
def _default_run_lock(run_id: str):
    with _LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(run_id, RLock())
    with lock:
        yield


def _persist_lock(runtime: Any, run_id: str):
    provider = getattr(runtime.services, "document_persist_lock", None)
    if provider is None:
        return _default_run_lock(run_id)
    return provider(run_id)


def _run_id(runtime: Any) -> str:
    value = runtime.manifest.get("runId") or runtime.manifest.get("run_id")
    if value:
        return str(value)
    value = getattr(runtime.services, "run_id", None)
    if value:
        return str(value)
    raise ValueError("Workflow Manifest has no Run identity")


def asset_persist_handler(
    state: DocumentState, params: Mapping[str, Any], runtime: Any
) -> OperatorResult:
    del params
    run_id = _run_id(runtime)
    repository = runtime.runtime_repository
    with _persist_lock(runtime, run_id):
        marker = repository.document_persist_marker(state.run_document_id)
        if marker is not None:
            document_id, snapshot_id = marker
            context = state.context.with_updates(
                document_id=document_id,
                snapshot_ref=snapshot_id,
                capability_facts=state.context.capability_facts
                | frozenset({"assets_persisted"}),
            )
            return OperatorResult(
                state.with_context(
                    context, capabilities=frozenset({"assets_persisted"})
                ),
                frozenset({"assets_persisted"}),
                OperatorStatus.SUCCESS,
            )

        # 批次8 M5（24 号 §5.8）：bundle → 三面资产持久化服务；legacy
        # persist_document_assets 已随兼容 DocumentContext 链退役。
        from ..bundle import MiningDocumentBundle

        if not isinstance(state.context, MiningDocumentBundle):
            return OperatorResult(
                state, frozenset(), OperatorStatus.FAILED,
                error_code="asset_persist_bad_input",
                error_message=(
                    "asset_persist requires a MiningDocumentBundle "
                    f"(got {type(state.context).__name__})"
                ),
            )
        service = getattr(runtime.services, "asset_persist_service", None)
        if service is None:
            return OperatorResult(
                state, frozenset(), OperatorStatus.FAILED,
                error_code="asset_persist_unavailable",
                error_message="asset_persist service is not configured on this runtime",
            )
        try:
            outcome = service.persist_for_snapshot(
                snapshot_id=state.context.snapshot_ref,
                document_ref=state.context.document_ref,
            )
        except Exception as exc:
            return OperatorResult(
                state,
                frozenset(),
                OperatorStatus.FAILED,
                warnings=(OperatorWarning("asset_persist_failed", str(exc)),),
                error_code="asset_persist_failed",
                error_message=str(exc),
            )
        commit_document = getattr(runtime.services, "commit_document", None)
        if commit_document is not None and state.context.document_id:
            commit_document(
                state.run_document_id,
                state.context.document_id,
                state.context.snapshot_ref or "",
            )
        persisted = state.context.with_updates(
            document_id=getattr(outcome, "document_id", None)
            or state.context.document_id,
            capability_facts=state.context.capability_facts
            | frozenset({"assets_persisted"}),
            diagnostics={
                **dict(state.context.diagnostics),
                "readiness": dict(getattr(outcome, "readiness", {}) or {}),
                "schema_version": getattr(outcome, "schema_version", None),
                "tokenizer_version": getattr(outcome, "tokenizer_version", None),
            },
        )

        capability = frozenset({"assets_persisted"})
        return OperatorResult(
            state.with_context(persisted, capabilities=capability),
            capability,
            OperatorStatus.SUCCESS,
        )
