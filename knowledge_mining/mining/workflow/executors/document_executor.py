from __future__ import annotations

import logging
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Lock
from typing import Iterable

from ..core import (
    DocumentState,
    ErrorPolicy,
    OperatorResult,
    OperatorStatus,
    OperatorWarning,
)
from ..execution_plan import ExecutionPlan, PlannedNode

logger = logging.getLogger(__name__)


class WorkflowRunFailed(RuntimeError):
    pass


class WorkflowCancelled(RuntimeError):
    pass


class WorkflowPaused(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentOutcome:
    state: DocumentState
    status: OperatorStatus
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class DocumentExecutionResult:
    outcomes: tuple[DocumentOutcome, ...]
    max_active_documents: int


_EVENT_STATUS = {
    OperatorStatus.SUCCESS: "completed",
    OperatorStatus.SKIPPED: "skipped",
    OperatorStatus.FALLBACK: "fallback",
    OperatorStatus.FAILED: "failed",
    OperatorStatus.PAUSED: "paused",
    OperatorStatus.NOT_APPLICABLE: "not_applicable",
}


class DocumentExecutor:
    """Bounded cross-document scheduler for the document section of a plan."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.repository = runtime.runtime_repository
        self.registry = runtime.services.handler_registry
        self._activity_lock = Lock()
        self._active_documents = 0
        self._max_active_documents = 0

    def execute(
        self,
        plan: ExecutionPlan,
        states: Iterable[DocumentState],
        *,
        max_workers: int,
    ) -> DocumentExecutionResult:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        indexed_outcomes: dict[int, DocumentOutcome] = {}
        iterator = iter(enumerate(states))
        futures: dict[Future[DocumentOutcome], int] = {}

        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="mining-document",
        ) as pool:
            for _ in range(max_workers):
                try:
                    index, state = next(iterator)
                except StopIteration:
                    break
                futures[pool.submit(self._run_active, plan, state)] = index

            while futures:
                completed, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                for future in completed:
                    index = futures.pop(future)
                    try:
                        indexed_outcomes[index] = future.result()
                    except Exception:
                        for pending in futures:
                            pending.cancel()
                        raise
                    try:
                        next_index, next_state = next(iterator)
                    except StopIteration:
                        continue
                    futures[pool.submit(self._run_active, plan, next_state)] = next_index

        return DocumentExecutionResult(
            tuple(indexed_outcomes[index] for index in sorted(indexed_outcomes)),
            self._max_active_documents,
        )

    def resume(
        self,
        plan: ExecutionPlan,
        states: Iterable[DocumentState],
        *,
        max_workers: int,
    ) -> DocumentExecutionResult:
        return self.execute(plan, states, max_workers=max_workers)

    def _run_active(
        self, plan: ExecutionPlan, state: DocumentState
    ) -> DocumentOutcome:
        with self._activity_lock:
            self._active_documents += 1
            self._max_active_documents = max(
                self._max_active_documents, self._active_documents
            )
        try:
            return self._execute_document(plan, state)
        finally:
            with self._activity_lock:
                self._active_documents -= 1

    def _execute_document(
        self, plan: ExecutionPlan, initial: DocumentState
    ) -> DocumentOutcome:
        run_id = self._run_id()
        persist_node = next(
            (
                plan.node(node_id)
                for node_id in plan.document_order
                if plan.node(node_id).operator_type == "asset_persist"
            ),
            None,
        )
        if persist_node is not None and self._is_committed(
            run_id, persist_node.node_id, initial.run_document_id
        ):
            document_id, snapshot_id = self.repository.document_persist_marker(
                initial.run_document_id
            )
            context = initial.context.with_updates(
                document_id=document_id,
                snapshot_id=snapshot_id,
            )
            return DocumentOutcome(
                initial.with_context(
                    context, capabilities=frozenset({"assets_persisted"})
                ),
                OperatorStatus.SUCCESS,
            )

        document_nodes = set(plan.document_order)
        predecessors: dict[str, list[str]] = {
            node_id: [] for node_id in plan.document_order
        }
        for edge in plan.edges:
            if edge.from_node in document_nodes and edge.to_node in document_nodes:
                predecessors[edge.to_node].append(edge.from_node)

        outputs: dict[str, DocumentState] = {}
        latest = initial
        for node_id in plan.document_order:
            self._check_cancellation()
            planned = plan.node(node_id)
            inbound = predecessors[node_id]
            if len(inbound) == 1:
                node_input = outputs[inbound[0]].fork()
            elif len(inbound) > 1:
                node_input = DocumentState.merge_batches([
                    (outputs[parent],) for parent in inbound
                ])[0].fork()
            else:
                node_input = latest.fork()

            result = self._execute_node(run_id, planned, node_input)
            outcome = self._apply_policy(planned, node_input, result)
            if isinstance(outcome, DocumentOutcome):
                return outcome
            latest = outcome
            outputs[node_id] = outcome

        # BUG-3（批次1）：链走完但从未 committed 的文档（如空内容从未入暂存），
        # 不能把 mining_run_documents 留在 processing——按 skipped 留痕。
        self._mark_run_document_terminal(
            latest, failed=False,
            message="document finished without asset persistence",
        )
        return DocumentOutcome(latest, OperatorStatus.SUCCESS)

    def _mark_run_document_terminal(
        self, state: DocumentState, *, failed: bool, message: str | None,
    ) -> None:
        """非 committed 文档的终态留痕（BUG-3）：经 services 注入的 sink 写
        mining_run_documents（fail_document/skip_document），已 committed 的
        文档由成功路径负责，不重复标记。"""
        sink = getattr(self.runtime.services, "mark_document_outcome", None)
        if sink is None:
            return
        run_document_id = getattr(state, "run_document_id", None)
        if not run_document_id:
            return
        try:
            if self.repository.document_persist_marker(run_document_id) is not None:
                return
            sink(
                run_document_id,
                "failed" if failed else "skipped",
                message or "",
            )
        except Exception:
            logger.exception(
                "Failed to mark terminal run document state for %s", run_document_id,
            )

    def _execute_node(
        self, run_id: str, planned: PlannedNode, state: DocumentState
    ) -> OperatorResult:
        attempt = self.repository.start_node(
            run_id=run_id,
            run_document_id=state.run_document_id,
            node_id=planned.node_id,
            operator_type=planned.operator_type,
            operator_version=planned.operator_version,
            input_summary={
                "docKey": state.doc_key,
                "capabilities": sorted(state.capabilities),
            },
        )
        if planned.guard == "ontology_applicable" and not self._ontology_applicable():
            capability = frozenset({"ontology_not_applicable"})
            result = OperatorResult(
                state.with_context(state.context, capabilities=capability),
                capability,
                OperatorStatus.NOT_APPLICABLE,
            )
        else:
            try:
                handler = self.registry.resolve(
                    planned.operator_type, planned.operator_version
                )
                result = handler(state, dict(planned.params), self.runtime)
                if not isinstance(result, OperatorResult):
                    raise TypeError(
                        f"Handler {planned.operator_type} returned {type(result).__name__}"
                    )
            except Exception as exc:
                result = OperatorResult(
                    state,
                    frozenset(),
                    OperatorStatus.FAILED,
                    warnings=(OperatorWarning("unexpected_handler_error", str(exc)),),
                    error_code="unexpected_handler_error",
                    error_message=str(exc),
                )

        self.repository.finish_node(
            attempt,
            status=self._terminal_event_status(planned, result),
            output_summary={
                "capabilities": sorted(result.capabilities),
                "warningCount": len(result.warnings),
            },
            error_code=result.error_code,
            error_message=result.error_message,
            metadata={
                "warnings": [
                    {"code": warning.code, "message": warning.message}
                    for warning in result.warnings
                ]
            },
        )
        return result

    @staticmethod
    def _terminal_event_status(
        planned: PlannedNode, result: OperatorResult
    ) -> str:
        if result.status is not OperatorStatus.FAILED:
            return _EVENT_STATUS[result.status]
        policy = ErrorPolicy(planned.error_policy)
        if policy is ErrorPolicy.SKIP_WITH_EMPTY:
            return "skipped"
        if policy is ErrorPolicy.FALLBACK:
            return "fallback"
        if policy is ErrorPolicy.PAUSE_FOR_REVIEW:
            return "paused"
        return "failed"

    def _apply_policy(
        self,
        planned: PlannedNode,
        state: DocumentState,
        result: OperatorResult,
    ) -> DocumentState | DocumentOutcome:
        output = result.outputs if isinstance(result.outputs, DocumentState) else state
        output = output.with_context(
            output.context, capabilities=result.capabilities
        )
        if result.status in {
            OperatorStatus.SUCCESS,
            OperatorStatus.FALLBACK,
            OperatorStatus.NOT_APPLICABLE,
        }:
            return output
        if result.status is OperatorStatus.PAUSED:
            raise WorkflowPaused(
                result.error_message or f"Paused at {planned.node_id}"
            )
        if result.status is OperatorStatus.SKIPPED:
            if ErrorPolicy(planned.error_policy) is ErrorPolicy.SKIP_WITH_EMPTY:
                return output.with_context(
                    output.context, capabilities=planned.provides
                )
            # BUG-3：真跳过（非空态续链）——run_doc 落 skipped 终态。
            self._mark_run_document_terminal(
                output, failed=False,
                message=result.error_code or f"skipped at {planned.node_id}",
            )
            return DocumentOutcome(
                output,
                OperatorStatus.SKIPPED,
                result.error_code,
                result.error_message,
            )

        policy = ErrorPolicy(planned.error_policy)
        if policy is ErrorPolicy.FAIL_FAST:
            raise WorkflowRunFailed(
                result.error_message or f"{planned.node_id} failed"
            )
        if policy is ErrorPolicy.SKIP_DOCUMENT:
            # BUG-3：节点失败但按策略跳过该文档（如 corrupt PDF 的
            # document_parse FAILED）——run_doc 落 failed 终态并留错误信息，
            # 否则永滞 processing、run 计数出现黑洞。
            self._mark_run_document_terminal(
                output, failed=True,
                message=result.error_message
                or result.error_code
                or f"{planned.node_id} failed",
            )
            return DocumentOutcome(
                output,
                OperatorStatus.SKIPPED,
                result.error_code,
                result.error_message,
            )
        if policy in {ErrorPolicy.SKIP_WITH_EMPTY, ErrorPolicy.FALLBACK}:
            fallback = output.with_context(
                output.context, capabilities=planned.provides
            )
            return fallback
        raise WorkflowPaused(result.error_message or f"Paused at {planned.node_id}")

    def _is_committed(
        self, run_id: str, node_id: str, run_document_id: str
    ) -> bool:
        marker = self.repository.document_persist_marker(run_document_id)
        return marker is not None and self.repository.is_node_completed(
            run_id, node_id, run_document_id
        )

    def _ontology_applicable(self) -> bool:
        frozen = self.runtime.manifest.get("ontologyApplicable")
        if frozen is not None:
            return bool(frozen)
        return self.runtime.ontology_version_id is not None

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
