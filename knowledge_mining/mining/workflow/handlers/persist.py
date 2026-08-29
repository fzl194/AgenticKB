from __future__ import annotations

from contextlib import contextmanager
from threading import Lock, RLock
from typing import Any, Mapping

from knowledge_mining.mining.pipeline import persist_document_assets

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
                snapshot_id=snapshot_id,
            )
            return OperatorResult(
                state.with_context(
                    context, capabilities=frozenset({"assets_persisted"})
                ),
                frozenset({"assets_persisted"}),
                OperatorStatus.SUCCESS,
            )

        # 批次8 M1：bundle 到达 persist 而 M5 三面持久化未落地前，显式
        # clean-fail——不把 bundle 喂给 legacy persister 造成 AttributeError。
        from ..bundle import MiningDocumentBundle

        if isinstance(state.context, MiningDocumentBundle):
            return OperatorResult(
                state, frozenset(), OperatorStatus.FAILED,
                error_code="asset_persist_bundle_unsupported",
                error_message=(
                    "asset_persist for MiningDocumentBundle lands in M5 "
                    "(three-projection persistence); legacy persister removed"
                ),
            )
        persister = getattr(
            runtime.services, "persist_document_assets", persist_document_assets
        )
        try:
            context = persister(
                state.context,
                runtime.services.pipeline_config,
                strict_embeddings=True,
                graph_store=getattr(runtime.services, "graph_store", None),
                ontology_store=getattr(runtime.services, "ontology_store", None),
                run_id=run_id,
                node_id="asset_persist",
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

        capability = frozenset({"assets_persisted"})
        return OperatorResult(
            state.with_context(context, capabilities=capability),
            capability,
            OperatorStatus.SUCCESS,
        )
