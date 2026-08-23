from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .core import DocumentState, OperatorResult, OperatorStatus
from .execution_plan import ExecutionPlan, PlannedNode
from .executors.document_executor import DocumentExecutor, WorkflowRunFailed
from .executors.global_executor import GlobalExecutor
from .graph import EdgeDef, WorkflowGraph
from .handler_registry import SAFE_RUN_OVERRIDES
from .manifest import graph_hash, value_hash


class InvalidWorkflowManifest(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowRuntimeResult:
    run_id: str
    status: str
    capabilities: frozenset[str]
    paused_at: str | None = None


class MiningWorkflowRuntime:
    """Execute one Run exclusively from its Domain-stored frozen Manifest."""

    SUPPORTED_SCHEMA_VERSION = "2.0"
    SUPPORTED_CATALOG_VERSION = "1"

    def __init__(self, context: Any, *, run_id: str) -> None:
        self.context = context
        self.run_id = run_id
        if not getattr(context.services, "run_id", None):
            context.services.run_id = run_id

    def execute(self) -> WorkflowRuntimeResult:
        manifest = self.context.runtime_repository.load_manifest(self.run_id)
        plan = self._verify_and_build_plan(manifest)
        document_states = self._execute_input(plan)
        document_result = DocumentExecutor(self.context).execute(
            plan,
            document_states,
            max_workers=int(getattr(self.context.services, "max_workers", 1)),
        )
        capabilities: set[str] = set()
        for outcome in document_result.outcomes:
            capabilities.update(outcome.state.capabilities)
        if not document_result.outcomes:
            capabilities.add("assets_persisted")
        self.context.services.initial_global_capabilities = frozenset(capabilities)
        global_result = GlobalExecutor(self.context).execute(plan)
        capabilities.update(global_result.capabilities)
        return WorkflowRuntimeResult(
            self.run_id,
            "awaiting_review" if global_result.paused else "completed",
            frozenset(capabilities),
            global_result.pause_step,
        )

    def resume(self) -> WorkflowRuntimeResult:
        return self.execute()

    def publish(self) -> WorkflowRuntimeResult:
        manifest = self.context.runtime_repository.load_manifest(self.run_id)
        plan = self._verify_and_build_plan(manifest)
        published = self._published_capabilities(plan)
        if published is not None:
            return WorkflowRuntimeResult(
                self.run_id,
                "completed",
                frozenset({"assets_persisted"}) | published,
            )
        if not self.context.services.claim_manual_publish():
            published = self._published_capabilities(plan)
            if published is not None:
                return WorkflowRuntimeResult(
                    self.run_id,
                    "completed",
                    frozenset({"assets_persisted"}) | published,
                )
            raise ValueError(
                f"Run {self.run_id} could not be claimed for publishing"
            )
        document_states = self._execute_input(plan)
        document_result = DocumentExecutor(self.context).execute(
            plan,
            document_states,
            max_workers=int(getattr(self.context.services, "max_workers", 1)),
        )
        capabilities: set[str] = set()
        for outcome in document_result.outcomes:
            capabilities.update(outcome.state.capabilities)
        if not document_result.outcomes:
            capabilities.add("assets_persisted")
        self.context.services.initial_global_capabilities = frozenset(capabilities)
        finalize_node_id = self._finalize_node_id(plan)
        global_result = GlobalExecutor(self.context).execute(
            plan,
            replay_nodes=(
                frozenset({finalize_node_id})
                if finalize_node_id is not None
                else frozenset()
            ),
        )
        capabilities.update(global_result.capabilities)
        return WorkflowRuntimeResult(
            self.run_id,
            "awaiting_review" if global_result.paused else "completed",
            frozenset(capabilities),
            global_result.pause_step,
        )

    @staticmethod
    def _finalize_node_id(plan: ExecutionPlan) -> str | None:
        return next(
            (
                node_id
                for node_id in plan.global_order
                if plan.node(node_id).operator_type == "mining_finalize"
            ),
            None,
        )

    def _published_capabilities(
        self, plan: ExecutionPlan
    ) -> frozenset[str] | None:
        finalize_node_id = self._finalize_node_id(plan)
        if finalize_node_id is None:
            return None
        loader = getattr(
            self.context.runtime_repository, "reusable_node_result", None
        )
        if loader is not None:
            reusable = loader(self.run_id, finalize_node_id, None)
            capabilities = frozenset(
                (reusable or {}).get("capabilities") or ()
            )
            if "release_published" in capabilities:
                return capabilities
        return None

    def _execute_input(self, plan: ExecutionPlan) -> tuple[DocumentState, ...]:
        value = getattr(self.context.services, "input_spec", None)
        for node_id in plan.input_order:
            planned = plan.node(node_id)
            attempt = self.context.runtime_repository.start_node(
                run_id=self.run_id,
                run_document_id=None,
                node_id=planned.node_id,
                operator_type=planned.operator_type,
                operator_version=planned.operator_version,
                input_summary={},
            )
            handler = self.context.services.handler_registry.resolve(
                planned.operator_type, planned.operator_version
            )
            result = handler(value, dict(planned.params), self.context)
            if not isinstance(result, OperatorResult):
                raise WorkflowRunFailed(
                    f"Input Handler {planned.operator_type} returned an invalid result"
                )
            status = {
                OperatorStatus.SUCCESS: "completed",
                OperatorStatus.SKIPPED: "skipped",
                OperatorStatus.FALLBACK: "fallback",
                OperatorStatus.FAILED: "failed",
                OperatorStatus.PAUSED: "paused",
                OperatorStatus.NOT_APPLICABLE: "not_applicable",
            }[result.status]
            self.context.runtime_repository.finish_node(
                attempt,
                status=status,
                output_summary={"capabilities": sorted(result.capabilities)},
                error_code=result.error_code,
                error_message=result.error_message,
                metadata={
                    "warnings": [
                        {"code": item.code, "message": item.message}
                        for item in result.warnings
                    ]
                },
            )
            if result.status is OperatorStatus.FAILED:
                raise WorkflowRunFailed(
                    result.error_message or f"{planned.operator_type} failed"
                )
            value = result.outputs
        if value is None:
            return ()
        states = tuple(value)
        if not all(isinstance(item, DocumentState) for item in states):
            raise WorkflowRunFailed("input_ingest did not return DocumentState values")
        return states

    def _verify_and_build_plan(self, manifest: dict[str, Any]) -> ExecutionPlan:
        if manifest.get("schemaVersion") != self.SUPPORTED_SCHEMA_VERSION:
            raise InvalidWorkflowManifest("Unsupported Workflow schema version")
        if manifest.get("catalogVersion") != self.SUPPORTED_CATALOG_VERSION:
            raise InvalidWorkflowManifest("Unsupported Workflow catalog version")
        graph = WorkflowGraph.from_dict(manifest.get("graph") or {})
        if graph_hash(graph) != manifest.get("graphHash"):
            raise InvalidWorkflowManifest("Workflow graph hash mismatch")

        manifest_nodes = manifest.get("nodes") or []
        run_overrides = dict(manifest.get("runOverrides") or {})
        unsafe_overrides = sorted(set(run_overrides) - SAFE_RUN_OVERRIDES)
        if unsafe_overrides:
            raise InvalidWorkflowManifest(
                "Unsafe Run override(s): " + ", ".join(unsafe_overrides)
            )
        nodes: list[PlannedNode] = []
        graph_nodes = {item.node_id: item for item in graph.nodes}
        seen: set[str] = set()
        for raw in manifest_nodes:
            node_id = str(raw.get("nodeId") or "")
            params = dict(raw.get("params") or {})
            if value_hash(params) != raw.get("paramsHash"):
                raise InvalidWorkflowManifest(
                    f"Workflow node {node_id} parameter hash mismatch"
                )
            if (
                raw.get("type") == "mining_finalize"
                and "publishOnPartialFailure" in run_overrides
            ):
                params["publishOnPartialFailure"] = bool(
                    run_overrides["publishOnPartialFailure"]
                )
            graph_node = graph_nodes.get(node_id)
            if graph_node is None:
                raise InvalidWorkflowManifest(
                    f"Workflow node {node_id} is absent from graph"
                )
            operator_type = str(raw.get("type") or "")
            operator_version = str(raw.get("version") or "")
            if (
                graph_node.operator_type != operator_type
                or graph_node.operator_version != operator_version
            ):
                raise InvalidWorkflowManifest(
                    f"Workflow node {node_id} contract differs from graph"
                )
            seen.add(node_id)
            nodes.append(PlannedNode(
                node_id=node_id,
                operator_type=operator_type,
                operator_version=operator_version,
                params=params,
                requires=frozenset(raw.get("requires") or []),
                provides=frozenset(raw.get("provides") or []),
                error_policy=str(raw.get("errorPolicy") or "FAIL_FAST"),
                guard=raw.get("guard"),
            ))
        if seen != set(graph_nodes):
            raise InvalidWorkflowManifest("Workflow Manifest node set mismatch")
        for planned in nodes:
            try:
                self.context.services.handler_registry.resolve(
                    planned.operator_type, planned.operator_version
                )
            except LookupError as exc:
                raise InvalidWorkflowManifest(str(exc)) from exc

        execution = manifest.get("executionPlan") or {}
        orders = (
            tuple(execution.get("inputOrder") or []),
            tuple(execution.get("documentOrder") or []),
            tuple(execution.get("globalOrder") or []),
        )
        ordered_nodes = set().union(*map(set, orders))
        if ordered_nodes != seen:
            raise InvalidWorkflowManifest("Workflow execution order node set mismatch")
        edges = tuple(
            EdgeDef.from_dict(item) for item in (manifest.get("edges") or [])
        )
        if edges != graph.edges:
            raise InvalidWorkflowManifest("Workflow edge Manifest differs from graph")
        return ExecutionPlan(
            graph=graph,
            nodes=tuple(nodes),
            edges=edges,
            input_order=orders[0],
            document_order=orders[1],
            global_order=orders[2],
            required_completion=frozenset(
                execution.get("requiredCompletion") or []
            ),
            schema_version=str(manifest["schemaVersion"]),
            catalog_version=str(manifest["catalogVersion"]),
        )
