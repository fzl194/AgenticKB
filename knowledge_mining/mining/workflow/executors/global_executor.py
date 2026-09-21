from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from ..core import (
    ErrorPolicy,
    OperatorResult,
    OperatorStatus,
    OperatorWarning,
)
from ..execution_plan import ExecutionPlan, PlannedNode
from .document_executor import WorkflowCancelled, WorkflowRunFailed


@dataclass(frozen=True)
class GlobalState:
    capabilities: frozenset[str] = frozenset()

    def with_capabilities(self, capabilities: frozenset[str]) -> "GlobalState":
        return GlobalState(self.capabilities | capabilities)


@dataclass(frozen=True)
class GlobalExecutionResult:
    capabilities: frozenset[str]
    statuses: Mapping[str, str] = field(default_factory=dict)
    paused: bool = False
    pause_step: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "statuses", MappingProxyType(dict(self.statuses)))

    def status_for(self, node_id: str) -> str | None:
        return self.statuses.get(node_id)


class GlobalExecutor:
    """Sequential executor for review, graph, and finalize nodes."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.repository = runtime.runtime_repository
        self.registry = runtime.services.handler_registry

    def execute(
        self,
        plan: ExecutionPlan,
        *,
        replay_nodes: frozenset[str] = frozenset(),
    ) -> GlobalExecutionResult:
        run_id = self._run_id()
        state = GlobalState(frozenset(getattr(
            self.runtime.services,
            "initial_global_capabilities",
            frozenset(),
        )))
        statuses: dict[str, str] = {}
        for node_id in plan.global_order:
            self._check_cancellation()
            planned = plan.node(node_id)
            reusable = (
                None
                if node_id in replay_nodes
                else self._reusable_result(run_id, planned)
            )
            if reusable is not None:
                state = state.with_capabilities(reusable[1])
                statuses[node_id] = reusable[0]
                continue

            attempt = self.repository.start_node(
                run_id=run_id,
                run_document_id=None,
                node_id=planned.node_id,
                operator_type=planned.operator_type,
                operator_version=planned.operator_version,
                input_summary={"capabilities": sorted(state.capabilities)},
            )
            result = self._call_handler(planned, state)
            event_status = self._terminal_event_status(planned, result)
            self.repository.finish_node(
                attempt,
                status=event_status,
                output_summary={
                    "capabilities": sorted(result.capabilities),
                    "warningCount": len(result.warnings),
                },
                error_code=result.error_code,
                error_message=result.error_message,
                metadata={
                    "warnings": [
                        {"code": item.code, "message": item.message}
                        for item in result.warnings
                    ]
                },
            )
            statuses[node_id] = event_status

            if result.status is OperatorStatus.PAUSED:
                self.repository.set_active_node(
                    run_id,
                    node_id,
                    planned.operator_type,
                    pause_step=planned.operator_type,
                )
                return GlobalExecutionResult(
                    state.capabilities,
                    statuses,
                    paused=True,
                    pause_step=planned.operator_type,
                )
            if result.status is OperatorStatus.FAILED:
                policy = ErrorPolicy(planned.error_policy)
                if policy is ErrorPolicy.FAIL_FAST:
                    raise WorkflowRunFailed(
                        result.error_message or f"{node_id} failed"
                    )
                if policy is ErrorPolicy.PAUSE_FOR_REVIEW:
                    self.repository.set_active_node(
                        run_id,
                        node_id,
                        planned.operator_type,
                        pause_step=planned.operator_type,
                    )
                    return GlobalExecutionResult(
                        state.capabilities,
                        statuses,
                        paused=True,
                        pause_step=planned.operator_type,
                    )
                state = state.with_capabilities(planned.provides)
                continue
            state = state.with_capabilities(result.capabilities)

        self.repository.set_active_node(run_id, None, None, pause_step=None)
        return GlobalExecutionResult(state.capabilities, statuses)

    def resume(self, plan: ExecutionPlan) -> GlobalExecutionResult:
        return self.execute(plan)

    def _reusable_result(
        self, run_id: str, planned: PlannedNode
    ) -> tuple[str, frozenset[str]] | None:
        loader = getattr(self.repository, "reusable_node_result", None)
        if loader is not None:
            result = loader(run_id, planned.node_id, None)
            if result is not None:
                return (
                    str(result["status"]),
                    frozenset(result.get("capabilities") or planned.provides),
                )
        if self.repository.is_node_completed(run_id, planned.node_id, None):
            return "completed", planned.provides
        return None

    def _call_handler(
        self, planned: PlannedNode, state: GlobalState
    ) -> OperatorResult:
        if planned.guard == "ontology_applicable" and not self._ontology_applicable():
            return OperatorResult(
                state,
                frozenset({"ontology_not_applicable"}),
                OperatorStatus.NOT_APPLICABLE,
            )
        try:
            handler = self.registry.resolve(
                planned.operator_type, planned.operator_version
            )
            result = handler(state, dict(planned.params), self.runtime)
            if not isinstance(result, OperatorResult):
                raise TypeError(
                    f"Handler {planned.operator_type} returned {type(result).__name__}"
                )
            return result
        except Exception as exc:
            return OperatorResult(
                state,
                frozenset(),
                OperatorStatus.FAILED,
                warnings=(OperatorWarning("unexpected_handler_error", str(exc)),),
                error_code="unexpected_handler_error",
                error_message=str(exc),
            )

    @staticmethod
    def _terminal_event_status(
        planned: PlannedNode, result: OperatorResult
    ) -> str:
        if result.status is OperatorStatus.SUCCESS:
            return "completed"
        if result.status is OperatorStatus.NOT_APPLICABLE:
            return "not_applicable"
        if result.status is OperatorStatus.PAUSED:
            return "paused"
        if result.status is OperatorStatus.SKIPPED:
            return "skipped"
        if result.status is OperatorStatus.FALLBACK:
            return "fallback"
        policy = ErrorPolicy(planned.error_policy)
        if policy is ErrorPolicy.FALLBACK:
            return "fallback"
        if policy is ErrorPolicy.SKIP_WITH_EMPTY:
            return "skipped"
        if policy is ErrorPolicy.PAUSE_FOR_REVIEW:
            return "paused"
        return "failed"

    def _ontology_applicable(self) -> bool:
        binding = self.runtime.manifest.get("runtimeBinding") or {}
        frozen = binding.get("ontologyApplicable")
        if frozen is not None:
            return bool(frozen)
        # 本体线已退役：无冻结标记时一律不适用。
        return False

    def _check_cancellation(self) -> None:
        check = self.runtime.cancellation_check
        if check is not None and check():
            raise WorkflowCancelled("Workflow Run was cancelled")

    def _run_id(self) -> str:
        value = self.runtime.manifest.get("runId") or self.runtime.manifest.get("run_id")
        value = value or getattr(self.runtime.services, "run_id", None)
        if not value:
            raise ValueError("Workflow Manifest has no Run identity")
        return str(value)
