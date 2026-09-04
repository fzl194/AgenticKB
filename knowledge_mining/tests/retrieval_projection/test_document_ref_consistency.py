"""A0-2（34 号 §P0）：document target_ref 与 structure node ref 的一致性契约.

此前两套投影对「文档节点」用了两个身份：
- retrieval 侧 document target_ref = ``{doc}#document``；
- structure 侧 document node ref = 裸 ``{doc}``。

搜索命中文档级结果后签发的 st_ 按 target_ref 编码——能解码，但 inspect /
children / descendants 按节点表精确匹配 ref 时找不到节点（"可解码但不可读"）。
本契约钉住：两个投影对 document 必须是同一个 ref，且一切指向文档节点的
parent 关系都使用该 ref。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentElementLink,
)
from knowledge_mining.mining.retrieval_projection.projector import (
    project_representations,
)
from knowledge_mining.mining.retrieval_projection.structure_projection import (
    project_structure,
)

DOC = "manual.md"


def _segments() -> tuple[CompiledSegment, ...]:
    return (
        CompiledSegment(
            segment_index=0, block_type="paragraph", raw_text="顶层说明正文。",
            heading_chain=(),  # 无章节 → 直接挂文档节点
            element_ids=("e0",), links=(SegmentElementLink(element_id="e0"),),
            token_count=10,
        ),
        CompiledSegment(
            segment_index=1, block_type="paragraph", raw_text="章节内正文。",
            heading_chain=((1, "运维手册"),),
            element_ids=("e1",), links=(SegmentElementLink(element_id="e1"),),
            token_count=10,
        ),
    )


def test_document_structure_node_ref_matches_retrieval_target_ref():
    """structure 的 document node ref 必须等于 retrieval 的 document target_ref."""
    reps = project_representations(_segments(), document_ref=DOC, snapshot_ref="s1")
    structure = project_structure(_segments(), document_ref=DOC)

    doc_targets = [r.target_ref for r in reps if r.target_type == "document"]
    assert doc_targets, "projector must emit a document representation"
    doc_nodes = [n for n in structure.nodes if n["node_type"] == "document"]
    assert doc_nodes, "structure projection must emit a document node"

    assert doc_nodes[0]["ref"] == doc_targets[0]


def test_document_children_parent_refs_point_at_document_node_ref():
    """挂在文档节点下的内容（无章节 segment / 无章节 table）parent_ref 必须命中
    document node 的真实 ref——children/descendants 才能解析。"""
    from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment

    table_seg = CompiledSegment(
        segment_index=2, block_type="table", raw_text="告警码=A；功耗=1",
        heading_chain=(),  # 无章节的表格 → 直接挂文档节点
        element_ids=("t0",), links=(SegmentElementLink(element_id="t0"),),
        metadata={"table_header": ["告警码", "功耗"], "table_ref": "tbl-1"},
        token_count=8,
    )
    segments = _segments() + (table_seg,)
    structure = project_structure(segments, document_ref=DOC)

    refs = {n["ref"] for n in structure.nodes}
    # 所有 parent_ref 都必须解析到节点表内的真实 ref（document 节点自身除外）
    non_doc_parents = {
        n["parent_ref"] for n in structure.nodes
        if n["node_type"] != "document" and n.get("parent_ref")
    }
    assert non_doc_parents <= refs, (
        f"parent_ref 指向不存在的节点：{non_doc_parents - refs}"
    )


def test_top_level_section_parent_is_document_node_ref():
    """顶层章节的 parent_ref 必须指向 document 节点的真实 ref（树不断裂）."""
    structure = project_structure(_segments(), document_ref=DOC)
    doc_ref = next(n["ref"] for n in structure.nodes if n["node_type"] == "document")
    top_sections = [
        n for n in structure.nodes
        if n["node_type"] == "section" and n.get("level") == 1
    ]
    assert top_sections, "fixture must contain a top-level section"
    assert all(s["parent_ref"] == doc_ref for s in top_sections)


def test_every_segment_node_resolvable_via_parent_chain():
    """每个 segment 节点的 parent_ref 必须是节点表内的真实 ref（无悬空）."""
    structure = project_structure(_segments(), document_ref=DOC)
    refs = {n["ref"] for n in structure.nodes}
    for node in structure.nodes:
        if node["node_type"] == "segment":
            assert node["parent_ref"] in refs, (
                f"segment {node['ref']} 悬空 parent {node['parent_ref']}"
            )
