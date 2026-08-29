"""批次8 M0 后的编译器契约：正式固定链 + 研究算子 clean-fail。

旧 7 类模板相关用例（模板边界/protected 自动恢复/ontology guard/
实体参数晋升）随模板与研究算子退役删除；对应用新的 M0 行为：
- 正式链 = input_ingest→document_parse→segment_compile→[embedding]→
  asset_persist→mining_finalize；
- 研究算子类型出现在图中 = unknown_operator 编译失败，不静默。
"""
from dataclasses import replace

from knowledge_mining.mining.workflow.compiler import WorkflowCompiler
from knowledge_mining.mining.workflow.graph import EdgeDef, NodeDef, OutputDef, WorkflowGraph
from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog


def _compile(graph):
    return WorkflowCompiler(builtin_catalog()).compile(graph, mode="publish")


def _node(operator_type: str) -> NodeDef:
    return NodeDef(node_id=operator_type, operator_type=operator_type, params={})


def _chain_graph(*, with_embedding: bool = False) -> WorkflowGraph:
    types = [
        "input_ingest",
        "document_parse",
        "segment_compile",
    ]
    edges = [
        EdgeDef("input_ingest", "rawFiles", "document_parse", "rawFiles"),
        EdgeDef("document_parse", "documents", "segment_compile", "documents"),
    ]
    if with_embedding:
        types.append("embedding")
        edges.append(
            EdgeDef("segment_compile", "documents", "embedding", "documents")
        )
        edges.append(
            EdgeDef("embedding", "documents", "asset_persist", "discourseAssets")
        )
    else:
        edges.append(
            EdgeDef("segment_compile", "documents", "asset_persist", "documents")
        )
    types.extend(["asset_persist", "mining_finalize"])
    edges.append(
        EdgeDef("asset_persist", "finalizeInput", "mining_finalize", "finalizeInput")
    )
    return WorkflowGraph(
        schema_version="2.0",
        nodes=tuple(_node(t) for t in types),
        edges=tuple(edges),
        output=OutputDef("mining_finalize", "result"),
    )


def test_formal_chain_compiles_into_input_document_and_global_zones() -> None:
    result = _compile(_chain_graph())
    assert result.valid is True
    assert result.plan is not None
    assert result.plan.input_order == ("input_ingest",)
    assert result.plan.document_order[:2] == ("document_parse", "segment_compile")
    assert result.plan.document_order[-1] == "asset_persist"
    assert result.plan.global_order == ("mining_finalize",)


def test_missing_fixed_operator_fails_cleanly() -> None:
    graph = _chain_graph()
    broken = replace(
        graph,
        nodes=tuple(
            node for node in graph.nodes if node.operator_type != "asset_persist"
        ),
    )
    invalid = _compile(broken)
    assert "missing_fixed_operator" in {error.kind for error in invalid.errors}


def test_research_operators_fail_as_unknown_not_silently() -> None:
    """研究算子（entity/ontology/graph_write）出现在正式图中必须编译失败。"""
    graph = _chain_graph()
    polluted = replace(
        graph,
        nodes=graph.nodes + (_node("entity_extract"),),
    )
    result = _compile(polluted)
    assert result.valid is False
    assert "unknown_operator" in {error.kind for error in result.errors}


def test_compiler_rejects_cycle() -> None:
    graph = _chain_graph(with_embedding=True)
    bad_edges = graph.edges + (
        EdgeDef("embedding", "documents", "segment_compile", "documents"),
    )
    result = _compile(replace(graph, edges=bad_edges))
    kinds = {error.kind for error in result.errors}
    assert "cycle" in kinds or "missing_capability" in kinds


def test_no_runtime_guard_is_attached_to_any_formal_node() -> None:
    plan = _compile(_chain_graph()).require_plan()
    assert all(node.guard is None for node in plan.nodes)


def test_publish_rejects_disabled_or_duplicate_fixed_nodes() -> None:
    graph = _chain_graph()
    disabled = replace(
        graph,
        nodes=tuple(
            replace(node, disabled=True)
            if node.operator_type == "document_parse"
            else node
            for node in graph.nodes
        ),
    )
    assert "disabled_fixed_operator" in {error.kind for error in _compile(disabled).errors}

    duplicate = replace(
        graph,
        nodes=graph.nodes + (NodeDef("parse-copy", "document_parse"),),
    )
    assert "duplicate_operator" in {error.kind for error in _compile(duplicate).errors}


def test_publish_rejects_incompatible_slots_and_orphan_document_output() -> None:
    graph = _chain_graph(with_embedding=True)
    incompatible = replace(
        graph,
        edges=graph.edges
        + (EdgeDef("input_ingest", "rawFiles", "embedding", "documents"),),
    )
    assert "incompatible_slot" in {error.kind for error in _compile(incompatible).errors}

    orphaned = replace(
        graph,
        edges=tuple(
            edge
            for edge in graph.edges
            if not (edge.from_node == "embedding" and edge.to_node == "asset_persist")
        ),
    )
    assert "orphan_document_output" in {error.kind for error in _compile(orphaned).errors}
