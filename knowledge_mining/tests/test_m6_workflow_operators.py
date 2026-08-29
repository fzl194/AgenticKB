"""M6.1 新算子目录 + 编译器版本感知骨架（RED 先行，SRS §10.2/A11）.

- catalog：``document_parse``（raw_files→parsed_documents）与
  ``segment_compile``（parsed_documents→parsed_segments）注册为 FIXED；
  旧 ``parse_segment`` 保留（v1 历史 manifest 兼容，§10.3）。
- compiler：骨架按 manifest schemaVersion 感知——v2 要求新两算子
  （不再要求 parse_segment），v1 维持原骨架；v2 强制 document_parse
  先于 segment_compile。
- 模板 v2：七套范式模板的解析段显式拆分为两算子（A11）。
- 参数档位：segment_compile 暴露切片策略字段（范式构建器头部面板）。
"""
from __future__ import annotations

from knowledge_mining.mining.workflow.compiler import WorkflowCompiler
from knowledge_mining.mining.workflow.graph import EdgeDef, NodeDef, WorkflowGraph
from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog


def _compile(graph):
    return WorkflowCompiler(builtin_catalog()).compile(graph, mode="publish")


# ---------------------------------------------------------------------------
# catalog：新算子定义
# ---------------------------------------------------------------------------


def test_catalog_defines_document_parse_and_segment_compile() -> None:
    catalog = builtin_catalog()
    parse = catalog["document_parse"]
    assert parse.edit_policy.value == "fixed"
    assert set(parse.requires) == {"raw_files"}
    assert set(parse.provides) == {"parsed_documents"}
    compile_op = catalog["segment_compile"]
    assert compile_op.edit_policy.value == "fixed"
    assert set(compile_op.requires) == {"parsed_documents"}
    assert set(compile_op.provides) == {"parsed_segments"}
    assert parse.display_name and compile_op.display_name


# ---------------------------------------------------------------------------
# 模板 v2 与编译骨架
# ---------------------------------------------------------------------------


def _node(operator_type: str) -> NodeDef:
    return NodeDef(node_id=operator_type, operator_type=operator_type, params={})


def _formal_chain_graph() -> WorkflowGraph:
    """批次8 M0 后的最小正式固定链（模板已清空，合成图验证编译骨架）。"""
    from knowledge_mining.mining.workflow.graph import OutputDef

    types = (
        "input_ingest",
        "document_parse",
        "segment_compile",
        "asset_persist",
        "mining_finalize",
    )
    edges = [
        EdgeDef("input_ingest", "rawFiles", "document_parse", "rawFiles"),
        EdgeDef("document_parse", "documents", "segment_compile", "documents"),
        EdgeDef("segment_compile", "documents", "asset_persist", "documents"),
        EdgeDef("asset_persist", "finalizeInput", "mining_finalize", "finalizeInput"),
    ]
    return WorkflowGraph(
        schema_version="2.0",
        nodes=tuple(_node(t) for t in types),
        edges=tuple(edges),
        output=OutputDef("mining_finalize", "result"),
    )


def test_formal_chain_compiles_with_split_parse_operators() -> None:
    result = _compile(_formal_chain_graph())
    assert result.valid is True, [e.kind for e in result.errors]
    order = result.require_plan().document_order
    assert order[0] == "document_parse", order
    assert order[1] == "segment_compile", order
    assert "parse_segment" not in order


def test_missing_segment_compile_is_fixed_error() -> None:
    graph = _formal_chain_graph()
    kept_nodes = tuple(
        n for n in graph.nodes if n.operator_type != "segment_compile"
    )
    kept_ids = {n.node_id for n in kept_nodes}
    kept_edges = tuple(
        e for e in graph.edges
        if e.from_node in kept_ids and e.to_node in kept_ids
    )
    broken = WorkflowGraph(
        schema_version=graph.schema_version, nodes=kept_nodes,
        edges=kept_edges, output=graph.output,
    )
    result = _compile(broken)
    assert result.valid is False
    codes = [e.kind for e in result.errors]
    assert "missing_fixed_operator" in codes
    assert any("segment_compile" in e.message for e in result.errors)


def test_formal_chain_does_not_require_parse_segment() -> None:
    """正式骨架不要求旧 parse_segment 算子（批次8 收口后目录里也没有它）。"""
    result = _compile(_formal_chain_graph())
    assert result.valid is True
    assert not any(
        "parse_segment" in e.message for e in result.errors
    )


def test_order_document_parse_before_segment_compile() -> None:
    """document_parse → segment_compile 固定骨架顺序校验（交换必须失败）."""
    graph = _formal_chain_graph()
    swapped = []
    for node in graph.nodes:
        if node.operator_type == "document_parse":
            swapped.append(
                NodeDef(
                    node_id=node.node_id, operator_type="segment_compile",
                    params=node.params,
                )
            )
        elif node.operator_type == "segment_compile":
            swapped.append(
                NodeDef(
                    node_id=node.node_id, operator_type="document_parse",
                    params=node.params,
                )
            )
        else:
            swapped.append(node)
    broken = WorkflowGraph(
        schema_version=graph.schema_version, nodes=tuple(swapped),
        edges=graph.edges, output=graph.output,
    )
    result = _compile(broken)
    # 交换后必然出现能力缺口或顺序问题——必须编译失败，不得静默通过。
    assert result.valid is False
    assert result.errors


# ---------------------------------------------------------------------------
# 参数档位（范式构建器头部面板的契约，R2/R5）
# ---------------------------------------------------------------------------


def test_segment_compile_options_expose_policy_fields() -> None:
    from knowledge_mining.mining.workflow.operators.options import (
        OPTIONS_BY_OPERATOR,
    )

    opts = OPTIONS_BY_OPERATOR["segment_compile"].model_validate(
        {"tableView": "rows", "maxTokens": 512}
    )
    data = opts.model_dump(by_alias=True)
    assert data["tableView"] == "rows"
    assert data["maxTokens"] == 512
    # 非法档位被拒（面板下拉的词表校验）。
    import pytest

    with pytest.raises(Exception):
        OPTIONS_BY_OPERATOR["segment_compile"].model_validate(
            {"tableView": "magic"}
        )


def test_document_parse_options_expose_quality_profile() -> None:
    from knowledge_mining.mining.workflow.operators.options import (
        OPTIONS_BY_OPERATOR,
    )

    opts = OPTIONS_BY_OPERATOR["document_parse"].model_validate(
        {"qualityProfile": "strict"}
    )
    assert opts.model_dump(by_alias=True)["qualityProfile"] == "strict"
