from __future__ import annotations

from knowledge_mining.mining.workflow.core import OperatorResult, OperatorStatus
from knowledge_mining.mining.workflow.executors.document_executor import (
    DocumentExecutor,
)
from knowledge_mining.mining.workflow.handler_registry import HandlerRegistry
from knowledge_mining.tests.test_mining_document_executor import (
    FakeEventRepository,
    document_state,
    node,
    plan,
    runtime as document_runtime,
)


def recovery_registry(repository: FakeEventRepository, calls: list[tuple]) -> HandlerRegistry:
    registry = HandlerRegistry()

    def parse(state, params, runtime):
        calls.append((state.run_document_id, "parse"))
        return OperatorResult(state, frozenset({"parsed"}), OperatorStatus.SUCCESS)

    def persist(state, params, runtime):
        calls.append((state.run_document_id, "asset_persist"))
        repository.markers[state.run_document_id] = (
            f"document-{state.run_document_id}",
            f"snapshot-{state.run_document_id}",
        )
        return OperatorResult(
            state, frozenset({"assets_persisted"}), OperatorStatus.SUCCESS
        )

    registry.register("parse", "1", parse)
    registry.register("asset_persist", "1", persist)
    return registry


def test_restart_skips_committed_documents_and_restarts_uncommitted_at_parse() -> None:
    repository = FakeEventRepository()
    calls: list[tuple] = []
    registry = recovery_registry(repository, calls)
    workflow = plan([node("parse"), node("asset_persist")])

    repository.markers["committed"] = ("document-committed", "snapshot-committed")
    repository.seed("committed", "asset_persist", "completed")
    repository.seed("interrupted", "parse", "started")

    result = DocumentExecutor(
        document_runtime(registry, repository)
    ).resume(
        workflow,
        [document_state("committed"), document_state("interrupted")],
        max_workers=2,
    )

    assert ("committed", "parse") not in calls
    assert ("committed", "asset_persist") not in calls
    assert calls.count(("interrupted", "parse")) == 1
    assert calls.count(("interrupted", "asset_persist")) == 1
    assert repository._attempts[("interrupted", "parse")] == 2
    assert result.outcomes[0].state.context.document_id == "document-committed"
