from __future__ import annotations

from collections import defaultdict
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.contracts.models import DocumentProfile, RawSegmentData
from knowledge_mining.mining.pipeline import DocumentContext
from knowledge_mining.mining.workflow.core import (
    DocumentState,
    OperatorResult,
    OperatorStatus,
)
from knowledge_mining.mining.workflow.execution_plan import ExecutionPlan, PlannedNode
from knowledge_mining.mining.workflow.executors.document_executor import (
    DocumentExecutor,
    WorkflowCancelled,
    WorkflowRunFailed,
)
from knowledge_mining.mining.workflow.graph import EdgeDef, OutputDef, WorkflowGraph
from knowledge_mining.mining.workflow.handler_registry import HandlerRegistry


def document_state(name: str) -> DocumentState:
    key = f"doc:/{name}"
    return DocumentState(
        name,
        key,
        DocumentContext(
            profile=DocumentProfile(document_key=key),
            segments=(RawSegmentData(
                document_key=key,
                segment_index=0,
                raw_text=name,
                metadata_json={"nested": {"value": 1}},
            ),),
            run_document_id=name,
        ),
    )


def node(
    node_id: str,
    *,
    error_policy: str = "FAIL_FAST",
    guard: str | None = None,
) -> PlannedNode:
    return PlannedNode(
        node_id=node_id,
        operator_type=node_id,
        operator_version="1",
        params={},
        requires=frozenset(),
        provides=frozenset({f"{node_id}_done"}),
        error_policy=error_policy,
        guard=guard,
    )


def plan(
    nodes: list[PlannedNode], edges: list[tuple[str, str]] | None = None
) -> ExecutionPlan:
    actual_edges = tuple(
        EdgeDef(source, "documents", target, "documents")
        for source, target in (edges or list(zip(
            [item.node_id for item in nodes[:-1]],
            [item.node_id for item in nodes[1:]],
        )))
    )
    graph = WorkflowGraph(
        nodes=(),
        edges=actual_edges,
        output=OutputDef(nodes[-1].node_id, "documents"),
    )
    return ExecutionPlan(
        graph=graph,
        nodes=tuple(nodes),
        edges=actual_edges,
        input_order=(),
        document_order=tuple(item.node_id for item in nodes),
        global_order=(),
        required_completion=frozenset(),
    )


class FakeEventRepository:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.markers: dict[str, tuple[str, str]] = {}
        self._attempts = defaultdict(int)
        self._lock = Lock()

    def start_node(self, **kwargs):
        key = (kwargs["run_document_id"], kwargs["node_id"])
        with self._lock:
            self._attempts[key] += 1
            attempt = SimpleNamespace(
                id=f"{key[0]}:{key[1]}:{self._attempts[key]}",
                attempt_no=self._attempts[key],
            )
            self.events.append({
                **kwargs,
                "attempt": attempt.attempt_no,
                "status": "started",
                "id": attempt.id,
            })
        return attempt

    def finish_node(self, attempt, **kwargs):
        with self._lock:
            matching = [event for event in self.events if event["id"] == attempt.id]
            assert len(matching) == 1
            assert matching[0]["status"] == "started"
            matching[0].update(kwargs)

    def is_node_completed(self, run_id, node_id, run_document_id):
        return any(
            event["run_id"] == run_id
            and event["run_document_id"] == run_document_id
            and event["node_id"] == node_id
            and event["status"] == "completed"
            for event in self.events
        )

    def document_persist_marker(self, run_document_id):
        return self.markers.get(run_document_id)

    def run_document_status(self, run_document_id):
        return self.run_document_statuses.get(run_document_id)

    def seed(self, document_id: str, node_id: str, status: str) -> None:
        attempt = self.start_node(
            run_id="run-1",
            run_document_id=document_id,
            node_id=node_id,
            operator_type=node_id,
            operator_version="1",
            input_summary={},
        )
        if status != "started":
            self.finish_node(attempt, status=status)


def runtime(registry: HandlerRegistry, repository=None, *, ontology="ontology-v1"):
    return SimpleNamespace(
        domain="odn",
        ontology_version_id=ontology,
        runtime_repository=repository or FakeEventRepository(),
        cancellation_check=lambda: False,
        services=SimpleNamespace(handler_registry=registry),
        manifest={"runId": "run-1", "ontologyApplicable": ontology is not None},
    )


def success_handler(calls, name, *, barrier=None):
    def handler(state, params, runtime):
        del params, runtime
        calls[state.run_document_id].append(name)
        if barrier is not None and name == "parse_segment":
            barrier.wait(timeout=2)
        return OperatorResult(
            state,
            frozenset({f"{name}_done"}),
            OperatorStatus.SUCCESS,
        )

    return handler


def test_documents_run_in_parallel_but_each_document_is_topological() -> None:
    calls = defaultdict(list)
    barrier = Barrier(2)
    registry = HandlerRegistry()
    for name in ("parse_segment", "enrich", "asset_persist"):
        registry.register(name, "1", success_handler(calls, name, barrier=barrier))

    result = DocumentExecutor(runtime(registry)).execute(
        plan([node("parse_segment"), node("enrich"), node("asset_persist")]),
        [document_state("a"), document_state("b")],
        max_workers=2,
    )

    assert calls == {
        "a": ["parse_segment", "enrich", "asset_persist"],
        "b": ["parse_segment", "enrich", "asset_persist"],
    }
    assert result.max_active_documents == 2
    assert [outcome.state.run_document_id for outcome in result.outcomes] == ["a", "b"]


def test_branch_state_is_copied_and_joined_by_document_identity() -> None:
    registry = HandlerRegistry()
    observed = {}

    def parse(state, params, runtime):
        return OperatorResult(state, frozenset({"parsed"}), OperatorStatus.SUCCESS)

    def left(state, params, runtime):
        state.context.segments[0].metadata_json["nested"]["value"] = 9
        return OperatorResult(state, frozenset({"left"}), OperatorStatus.SUCCESS)

    def right(state, params, runtime):
        observed["right"] = state.context.segments[0].metadata_json["nested"]["value"]
        return OperatorResult(state, frozenset({"right"}), OperatorStatus.SUCCESS)

    def join(state, params, runtime):
        observed["capabilities"] = state.capabilities
        return OperatorResult(state, frozenset({"joined"}), OperatorStatus.SUCCESS)

    for name, handler in {
        "parse": parse,
        "left": left,
        "right": right,
        "join": join,
    }.items():
        registry.register(name, "1", handler)

    workflow = plan(
        [node("parse"), node("left"), node("right"), node("join")],
        [("parse", "left"), ("parse", "right"), ("left", "join"), ("right", "join")],
    )
    original = document_state("a")

    DocumentExecutor(runtime(registry)).execute(workflow, [original], max_workers=1)

    assert observed["right"] == 1
    assert observed["capabilities"] >= {"left", "right"}
    assert original.context.segments[0].metadata_json["nested"]["value"] == 1


@pytest.mark.parametrize(
    ("policy", "expect_next", "raises", "event_status"),
    [
        ("FAIL_FAST", False, WorkflowRunFailed, "failed"),
        ("SKIP_DOCUMENT", False, None, "failed"),
        ("SKIP_WITH_EMPTY", True, None, "skipped"),
        ("FALLBACK", True, None, "fallback"),
    ],
)
def test_executor_owns_error_policy(policy, expect_next, raises, event_status) -> None:
    calls = []
    registry = HandlerRegistry()

    def failed(state, params, runtime):
        calls.append("failed")
        return OperatorResult(
            state,
            frozenset(),
            OperatorStatus.FAILED,
            error_code="boom",
            error_message="boom",
        )

    def next_handler(state, params, runtime):
        calls.append("next")
        return OperatorResult(state, frozenset({"next"}), OperatorStatus.SUCCESS)

    registry.register("failed", "1", failed)
    registry.register("next", "1", next_handler)
    repository = FakeEventRepository()
    executor = DocumentExecutor(runtime(registry, repository))
    workflow = plan([node("failed", error_policy=policy), node("next")])

    if raises:
        with pytest.raises(raises):
            executor.execute(workflow, [document_state("a")], max_workers=1)
    else:
        executor.execute(workflow, [document_state("a")], max_workers=1)

    assert ("next" in calls) is expect_next
    assert repository.events[0]["status"] == event_status


def test_false_ontology_guard_records_not_applicable_without_calling_handler() -> None:
    registry = HandlerRegistry()
    registry.register(
        "entity_extract",
        "1",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    repository = FakeEventRepository()

    result = DocumentExecutor(
        runtime(registry, repository, ontology=None)
    ).execute(
        plan([node("entity_extract", guard="ontology_applicable")]),
        [document_state("a")],
        max_workers=1,
    )

    assert result.outcomes[0].state.capabilities == {"ontology_not_applicable"}
    assert repository.events[0]["status"] == "not_applicable"


def test_skip_with_empty_handler_status_continues_downstream() -> None:
    calls = []
    registry = HandlerRegistry()
    registry.register(
        "optional",
        "1",
        lambda state, params, runtime: OperatorResult(
            state, frozenset(), OperatorStatus.SKIPPED
        ),
    )

    def downstream(state, params, runtime):
        calls.append(state.capabilities)
        return OperatorResult(state, frozenset({"done"}), OperatorStatus.SUCCESS)

    registry.register("downstream", "1", downstream)
    DocumentExecutor(runtime(registry)).execute(
        plan([
            node("optional", error_policy="SKIP_WITH_EMPTY"),
            node("downstream"),
        ]),
        [document_state("a")],
        max_workers=1,
    )

    assert calls == [frozenset({"optional_done"})]


def test_cancellation_is_checked_between_nodes() -> None:
    calls = defaultdict(list)
    registry = HandlerRegistry()
    registry.register("first", "1", success_handler(calls, "first"))
    registry.register("second", "1", success_handler(calls, "second"))
    context = runtime(registry)
    checks = iter([False, True])
    context.cancellation_check = lambda: next(checks)

    with pytest.raises(WorkflowCancelled):
        DocumentExecutor(context).execute(
            plan([node("first"), node("second")]),
            [document_state("a")],
            max_workers=1,
        )

    assert calls["a"] == ["first"]


def test_resume_skips_only_committed_asset_persist_documents() -> None:
    calls = defaultdict(list)
    registry = HandlerRegistry()
    for name in ("parse_segment", "asset_persist"):
        registry.register(name, "1", success_handler(calls, name))
    repository = FakeEventRepository()
    repository.seed("a", "asset_persist", "completed")
    repository.markers["a"] = ("document-a", "snapshot-a")
    repository.seed("b", "asset_persist", "started")
    repository.seed("b", "parse_segment", "failed")

    DocumentExecutor(runtime(registry, repository)).resume(
        plan([node("parse_segment"), node("asset_persist")]),
        [document_state("a"), document_state("b")],
        max_workers=2,
    )

    assert calls["a"] == []
    assert calls["b"] == ["parse_segment", "asset_persist"]
    parse_events = [
        event for event in repository.events
        if event["run_document_id"] == "b" and event["node_id"] == "parse_segment"
    ]
    assert [event["attempt"] for event in parse_events] == [1, 2]
    assert all(event["status"] != "started" for event in repository.events[-2:])


def test_ingest_skip_document_fast_paths_with_capability() -> None:
    """36号（E2E D' 追溯）：KB 增量 SKIP 文档带 serving 快照 identity，
    无任何 node event——executor 必须零算子执行
    地快路径返回 SUCCESS 并携带 assets_persisted，否则「SKIP carry + 其余
    文档全失败」的 run 会被 finalize 的 capabilities 门禁误拦。"""
    calls = defaultdict(list)
    registry = HandlerRegistry()
    for name in ("parse_segment", "asset_persist"):
        registry.register(name, "1", success_handler(calls, name))
    repository = FakeEventRepository()
    # SKIP 文档：marker（identity）+ 真实 skipped 状态，但从未跑过节点
    repository.markers["s"] = ("document-skip", "snapshot-skip")
    repository.run_document_statuses = {"s": "skipped"}

    outcome = DocumentExecutor(runtime(registry, repository)).execute(
        plan([node("parse_segment"), node("asset_persist")]),
        [document_state("s")],
        max_workers=1,
    ).outcomes[0]

    assert calls["s"] == []  # 零算子执行
    assert outcome.status is OperatorStatus.SUCCESS
    assert "assets_persisted" in outcome.state.capabilities
    assert outcome.state.context.document_id == "document-skip"
    assert outcome.state.context.snapshot_id == "snapshot-skip"


def test_resume_rejected_document_replays_chain_despite_old_persist_marker() -> None:
    """finalize-rejected 行再次 resume 时不得复用上次 completed persist。"""
    calls = defaultdict(list)
    registry = HandlerRegistry()
    for name in ("parse_segment", "asset_persist"):
        registry.register(name, "1", success_handler(calls, name))
    repository = FakeEventRepository()
    repository.markers["r"] = ("document-r", "snapshot-r")
    repository.run_document_statuses = {"r": "processing"}
    repository.seed("r", "asset_persist", "completed")

    state = document_state("r")
    state = type(state)(
        state.run_document_id, state.doc_key, state.context,
        state.capabilities, ("retry_rejected",),
    )
    outcome = DocumentExecutor(runtime(registry, repository)).execute(
        plan([node("parse_segment"), node("asset_persist")]),
        [state], max_workers=1,
    ).outcomes[0]

    assert calls["r"] == ["parse_segment", "asset_persist"]
    assert outcome.status is OperatorStatus.SUCCESS
