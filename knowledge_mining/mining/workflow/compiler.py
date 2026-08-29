from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import ValidationError

from .core import ExecutionZone, MiningOperatorDef, SlotType
from .execution_plan import ExecutionPlan, PlannedNode
from .graph import EdgeDef, WorkflowGraph
from .normalizer import WorkflowNormalizer, required_protected_types
from .operators.options import OPTIONS_BY_OPERATOR


FIXED_TYPES_V2 = {
    "input_ingest", "document_parse", "segment_compile",
    "asset_persist", "mining_finalize",
}


def _fixed_types_for(schema_version: str) -> set[str]:
    del schema_version
    return FIXED_TYPES_V2
# 批次8 M2：embedding 依赖恢复（retrieval_unit_project 提供检索表示）。
# 研究算子依赖保持移除（24 号 §5.12-§5.16）。
DEPENDENCIES: dict[str, set[str]] = {
    "embedding": {"retrieval_unit_project"},
}


@dataclass(frozen=True)
class CompileError:
    kind: str
    message: str
    node_id: str | None = None
    slot: str | None = None
    edge: str | None = None
    rule: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "kind": self.kind,
                "message": self.message,
                "nodeId": self.node_id,
                "slot": self.slot,
                "edge": self.edge,
                "rule": self.rule,
            }.items()
            if value is not None
        }


class WorkflowCompileException(ValueError):
    def __init__(self, errors: tuple[CompileError, ...]) -> None:
        super().__init__("Workflow compilation failed")
        self.errors = errors


@dataclass(frozen=True)
class CompileResult:
    errors: tuple[CompileError, ...] = ()
    plan: ExecutionPlan | None = None

    @property
    def valid(self) -> bool:
        return not self.errors and self.plan is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [error.to_dict() for error in self.errors],
            "executionPlan": None
            if self.plan is None
            else {
                "inputOrder": list(self.plan.input_order),
                "documentOrder": list(self.plan.document_order),
                "globalOrder": list(self.plan.global_order),
            },
        }

    def require_plan(self) -> ExecutionPlan:
        if self.plan is None or self.errors:
            raise WorkflowCompileException(self.errors)
        return self.plan


class WorkflowCompiler:
    def __init__(self, catalog: Mapping[str, MiningOperatorDef]) -> None:
        self._catalog = dict(catalog)
        self._normalizer = WorkflowNormalizer(self._catalog)

    def compile(
        self,
        graph: WorkflowGraph,
        *,
        mode: Literal["draft", "publish"] = "draft",
    ) -> CompileResult:
        errors: list[CompileError] = []
        if mode not in {"draft", "publish"}:
            return CompileResult((CompileError("invalid_mode", f"Unknown mode: {mode}"),))
        if graph.schema_version != "2.0":
            return CompileResult((CompileError(
                "unsupported_schema_version",
                "Only workflow schemaVersion 2.0 is supported",
            ),))
        normalized = self._normalizer.normalize(graph)
        if not normalized.nodes:
            errors.append(CompileError("empty_graph", "Workflow must contain nodes"))

        node_id_counts = Counter(node.node_id for node in normalized.nodes)
        for node_id, count in node_id_counts.items():
            if not node_id or count > 1:
                errors.append(
                    CompileError(
                        "duplicate_node_id" if node_id else "missing_node_id",
                        f"nodeId '{node_id}' appears {count} times",
                        node_id=node_id or None,
                    )
                )

        nodes = {node.node_id: node for node in normalized.nodes}
        type_counts = Counter(node.operator_type for node in normalized.nodes)
        definitions: dict[str, MiningOperatorDef] = {}
        effective_params: dict[str, dict[str, Any]] = {}
        for node in normalized.nodes:
            definition = self._catalog.get(node.operator_type)
            if definition is None:
                errors.append(
                    CompileError(
                        "unknown_operator",
                        f"Unknown operator '{node.operator_type}'",
                        node_id=node.node_id,
                    )
                )
                continue
            definitions[node.node_id] = definition
            if node.operator_version != definition.version:
                errors.append(
                    CompileError(
                        "unsupported_operator_version",
                        f"Unsupported {node.operator_type}@{node.operator_version}",
                        node_id=node.node_id,
                    )
                )
            if definition.unique and type_counts[node.operator_type] > 1:
                errors.append(
                    CompileError(
                        "duplicate_operator",
                        f"Operator '{node.operator_type}' must be unique",
                        node_id=node.node_id,
                    )
                )
            try:
                options = OPTIONS_BY_OPERATOR[node.operator_type].model_validate(
                    dict(node.params)
                )
                effective_params[node.node_id] = options.model_dump(by_alias=True)
            except ValidationError as exc:
                for item in exc.errors(include_url=False):
                    errors.append(
                        CompileError(
                            "invalid_params",
                            item["msg"],
                            node_id=node.node_id,
                            rule=".".join(str(part) for part in item["loc"]),
                        )
                    )

        if mode == "publish":
            errors.extend(self._validate_operator_presence(normalized, type_counts))

        enabled = {
            node_id: node
            for node_id, node in nodes.items()
            if not node.disabled and node_id in definitions
        }
        valid_edges = self._validate_edges(
            normalized.edges,
            nodes,
            enabled,
            definitions,
            errors,
        )
        errors.extend(self._validate_required_inputs(enabled, definitions, valid_edges))
        errors.extend(self._validate_output(normalized, nodes, definitions))

        topological_order, has_cycle = self._topological_order(enabled, valid_edges)
        if has_cycle:
            errors.append(CompileError("cycle", "Workflow graph must be acyclic"))

        if mode == "publish" and not has_cycle:
            errors.extend(
                self._validate_semantics(
                    normalized,
                    enabled,
                    definitions,
                    effective_params,
                    valid_edges,
                    topological_order,
                )
            )

        if errors:
            return CompileResult(tuple(self._deduplicate_errors(errors)))

        planned_nodes = tuple(
            PlannedNode(
                node_id=node_id,
                operator_type=enabled[node_id].operator_type,
                operator_version=enabled[node_id].operator_version,
                params=effective_params[node_id],
                requires=self._effective_requires(
                    enabled[node_id].operator_type, effective_params[node_id]
                ),
                provides=definitions[node_id].provides,
                error_policy=definitions[node_id].error_policy.value,
                guard=None,
            )
            for node_id in topological_order
        )
        input_order = tuple(
            node_id
            for node_id in topological_order
            if definitions[node_id].zone is ExecutionZone.INPUT
        )
        document_order = tuple(
            node_id
            for node_id in topological_order
            if definitions[node_id].zone is ExecutionZone.DOCUMENT
        )
        global_order = tuple(
            node_id
            for node_id in topological_order
            if definitions[node_id].zone is ExecutionZone.GLOBAL
        )
        required_completion = {"assets_persisted", "finalized"}
        return CompileResult(
            plan=ExecutionPlan(
                graph=normalized,
                nodes=planned_nodes,
                edges=tuple(valid_edges),
                input_order=input_order,
                document_order=document_order,
                global_order=global_order,
                required_completion=frozenset(required_completion),
                schema_version=normalized.schema_version,
                catalog_version="1",
            )
        )

    def _validate_operator_presence(
        self,
        graph: WorkflowGraph,
        type_counts: Counter[str],
    ) -> list[CompileError]:
        errors: list[CompileError] = []
        nodes_by_type = {node.operator_type: node for node in graph.nodes}
        for operator_type in _fixed_types_for(graph.schema_version):
            if type_counts[operator_type] == 0:
                errors.append(
                    CompileError(
                        "missing_fixed_operator",
                        f"Missing fixed operator '{operator_type}'",
                    )
                )
            elif nodes_by_type[operator_type].disabled:
                errors.append(
                    CompileError(
                        "disabled_fixed_operator",
                        f"Fixed operator '{operator_type}' cannot be disabled",
                        node_id=nodes_by_type[operator_type].node_id,
                    )
                )
        # 批次8 M0：protected 算子自动注入与校验路径整体移除；
        # 研究算子类型不再出现在正式图中，出现即按未知算子失败。
        return errors

    def _validate_edges(
        self,
        edges: tuple[EdgeDef, ...],
        nodes: dict[str, Any],
        enabled: dict[str, Any],
        definitions: dict[str, MiningOperatorDef],
        errors: list[CompileError],
    ) -> list[EdgeDef]:
        result: list[EdgeDef] = []
        seen: set[tuple[str, str, str, str]] = set()
        target_counts: Counter[tuple[str, str]] = Counter()
        for edge in edges:
            label = self._edge_label(edge)
            key = (edge.from_node, edge.from_slot, edge.to_node, edge.to_slot)
            if key in seen:
                errors.append(CompileError("duplicate_edge", "Duplicate edge", edge=label))
                continue
            seen.add(key)
            if edge.from_node not in nodes or edge.to_node not in nodes:
                errors.append(
                    CompileError("missing_node", "Edge references a missing node", edge=label)
                )
                continue
            if edge.from_node not in enabled or edge.to_node not in enabled:
                continue
            source_def = definitions[edge.from_node]
            target_def = definitions[edge.to_node]
            output_slot = source_def.output_slot(edge.from_slot)
            input_slot = target_def.input_slot(edge.to_slot)
            if output_slot is None:
                errors.append(
                    CompileError(
                        "missing_slot",
                        f"Unknown output slot '{edge.from_slot}'",
                        node_id=edge.from_node,
                        slot=edge.from_slot,
                        edge=label,
                    )
                )
            if input_slot is None:
                errors.append(
                    CompileError(
                        "missing_slot",
                        f"Unknown input slot '{edge.to_slot}'",
                        node_id=edge.to_node,
                        slot=edge.to_slot,
                        edge=label,
                    )
                )
            if output_slot is None or input_slot is None:
                continue
            if output_slot.type is not input_slot.type:
                errors.append(
                    CompileError(
                        "incompatible_slot",
                        f"{output_slot.type.value} cannot feed {input_slot.type.value}",
                        node_id=edge.to_node,
                        edge=label,
                    )
                )
                continue
            target_counts[(edge.to_node, edge.to_slot)] += 1
            result.append(edge)

        for (node_id, slot_name), count in target_counts.items():
            slot = definitions[node_id].input_slot(slot_name)
            if slot is not None and not slot.variadic and count > 1:
                errors.append(
                    CompileError(
                        "multiple_input_edges",
                        f"Input {node_id}.{slot_name} accepts one edge",
                        node_id=node_id,
                        slot=slot_name,
                    )
                )
        return result

    @staticmethod
    def _validate_required_inputs(
        enabled: dict[str, Any],
        definitions: dict[str, MiningOperatorDef],
        edges: list[EdgeDef],
    ) -> list[CompileError]:
        inbound = {(edge.to_node, edge.to_slot) for edge in edges}
        errors: list[CompileError] = []
        for node_id in enabled:
            definition = definitions[node_id]
            for slot in definition.input_slots:
                externally_satisfied = (
                    definition.zone is ExecutionZone.INPUT
                    and slot.type is SlotType.INPUT_SPEC
                )
                if slot.required and not externally_satisfied and (node_id, slot.name) not in inbound:
                    errors.append(
                        CompileError(
                            "missing_required_input",
                            f"Required input {node_id}.{slot.name} is not connected",
                            node_id=node_id,
                            slot=slot.name,
                        )
                    )
        return errors

    @staticmethod
    def _validate_output(
        graph: WorkflowGraph,
        nodes: dict[str, Any],
        definitions: dict[str, MiningOperatorDef],
    ) -> list[CompileError]:
        node = nodes.get(graph.output.node_id)
        if (
            node is None
            or node.disabled
            or node.operator_type != "mining_finalize"
            or graph.output.slot != "result"
            or graph.output.node_id not in definitions
        ):
            return [
                CompileError(
                    "invalid_output",
                    "Workflow output must be mining_finalize.result",
                    node_id=graph.output.node_id or None,
                )
            ]
        return []

    def _validate_semantics(
        self,
        graph: WorkflowGraph,
        enabled: dict[str, Any],
        definitions: dict[str, MiningOperatorDef],
        params: dict[str, dict[str, Any]],
        edges: list[EdgeDef],
        order: list[str],
    ) -> list[CompileError]:
        errors: list[CompileError] = []
        enabled_by_type = {
            node.operator_type: node for node in enabled.values()
        }
        for dependent_type, prerequisites in DEPENDENCIES.items():
            dependent = enabled_by_type.get(dependent_type)
            if dependent is None:
                continue
            ancestors = self._ancestors_of(dependent.node_id, edges)
            for prerequisite_type in prerequisites:
                prerequisite = enabled_by_type.get(prerequisite_type)
                if prerequisite is None or prerequisite.node_id not in ancestors:
                    errors.append(
                        CompileError(
                            "missing_operator_dependency",
                            f"{dependent_type} requires {prerequisite_type}",
                            node_id=dependent.node_id,
                        )
                    )

        for edge in edges:
            source_zone = definitions[edge.from_node].zone
            target_zone = definitions[edge.to_node].zone
            source_type = enabled[edge.from_node].operator_type
            if source_zone is ExecutionZone.GLOBAL and target_zone is not ExecutionZone.GLOBAL:
                errors.append(
                    CompileError(
                        "invalid_zone_transition",
                        "Global nodes cannot feed document/input nodes",
                        edge=self._edge_label(edge),
                    )
                )
            if (
                source_zone is ExecutionZone.DOCUMENT
                and target_zone is ExecutionZone.GLOBAL
                and source_type != "asset_persist"
            ):
                errors.append(
                    CompileError(
                        "invalid_zone_transition",
                        "Document/global transition must pass through asset_persist",
                        edge=self._edge_label(edge),
                    )
                )

        asset = enabled_by_type.get("asset_persist")
        if asset is not None:
            reaches_asset = self._ancestors_of(asset.node_id, edges)
            for node_id, node in enabled.items():
                if (
                    definitions[node_id].zone is ExecutionZone.DOCUMENT
                    and node.operator_type != "asset_persist"
                    and node_id not in reaches_asset
                ):
                    errors.append(
                        CompileError(
                            "orphan_document_output",
                            f"Document node '{node_id}' does not reach asset_persist",
                            node_id=node_id,
                        )
                    )

        capabilities: dict[str, frozenset[str]] = {}
        incoming: dict[str, list[str]] = {node_id: [] for node_id in enabled}
        for edge in edges:
            incoming[edge.to_node].append(edge.from_node)
        for node_id in order:
            node = enabled[node_id]
            available: set[str] = set()
            for predecessor in incoming[node_id]:
                available.update(capabilities.get(predecessor, frozenset()))
            if definitions[node_id].zone is ExecutionZone.INPUT:
                available.add("input_spec")
            requires = self._effective_requires(node.operator_type, params[node_id])
            missing = requires - available
            if missing:
                errors.append(
                    CompileError(
                        "missing_capability",
                        f"Missing capabilities: {', '.join(sorted(missing))}",
                        node_id=node_id,
                    )
                )
            capabilities[node_id] = frozenset(available | definitions[node_id].provides)

        output_ancestors = self._ancestors_of(graph.output.node_id, edges)
        output_ancestors.add(graph.output.node_id)
        for node_id in enabled:
            if node_id not in output_ancestors:
                errors.append(
                    CompileError(
                        "orphan_node",
                        f"Node '{node_id}' does not contribute to the output",
                        node_id=node_id,
                    )
                )
        return errors

    def _effective_requires(
        self,
        operator_type: str,
        params: Mapping[str, Any],
    ) -> frozenset[str]:
        requires = set(self._catalog[operator_type].requires)
        if operator_type == "entity_relation_extract" and params.get(
            "requireResolvedEntities"
        ):
            requires.add("resolved_entities")
        return frozenset(requires)

    @staticmethod
    def _topological_order(
        nodes: dict[str, Any], edges: list[EdgeDef]
    ) -> tuple[list[str], bool]:
        order_index = {node_id: index for index, node_id in enumerate(nodes)}
        indegree = {node_id: 0 for node_id in nodes}
        outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        for edge in edges:
            outgoing[edge.from_node].append(edge.to_node)
            indegree[edge.to_node] += 1
        ready = deque(node_id for node_id in nodes if indegree[node_id] == 0)
        result: list[str] = []
        while ready:
            node_id = ready.popleft()
            result.append(node_id)
            for target in sorted(outgoing[node_id], key=order_index.__getitem__):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        return result, len(result) != len(nodes)

    @staticmethod
    def _ancestors_of(node_id: str, edges: list[EdgeDef]) -> set[str]:
        reverse: dict[str, set[str]] = {}
        for edge in edges:
            reverse.setdefault(edge.to_node, set()).add(edge.from_node)
        visited: set[str] = set()
        stack = list(reverse.get(node_id, ()))
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            stack.extend(reverse.get(current, ()))
        return visited

    @staticmethod
    def _edge_label(edge: EdgeDef) -> str:
        return f"{edge.from_node}.{edge.from_slot} -> {edge.to_node}.{edge.to_slot}"

    @staticmethod
    def _deduplicate_errors(errors: list[CompileError]) -> list[CompileError]:
        result: list[CompileError] = []
        seen: set[tuple[Any, ...]] = set()
        for error in errors:
            key = (
                error.kind,
                error.message,
                error.node_id,
                error.slot,
                error.edge,
                error.rule,
            )
            if key not in seen:
                seen.add(key)
                result.append(error)
        return result
