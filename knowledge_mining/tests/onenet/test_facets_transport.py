# -*- coding: utf-8 -*-
"""facets 搬运（47 号 §六）：投影器 document_facets 参数（组合根接线后置）."""
from __future__ import annotations

from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment
from knowledge_mining.mining.retrieval_projection.projector import (
    project_representations,
)


def _segment(block_type="paragraph", text="正文内容", index=0):
    return CompiledSegment(
        segment_index=index,
        block_type=block_type,
        raw_text=text,
        normalized_text=text,
        token_count=8,
        element_ids=(f"e{index}",),
        heading_chain=((1, "章节一"),),
    )


def test_document_facets_merged_into_representations():
    reps = project_representations(
        [_segment()],
        document_ref="onenet:DOC1:abc",
        snapshot_ref="snap-1",
        document_facets={"language": "cn", "product_line": ["云核心网"],
                         "category": ["产品文档"]},
    )
    for rep in reps:
        assert rep.facets.get("language") == "cn"
        assert rep.facets.get("product_line") == ["云核心网"]
        assert rep.facets.get("category") == ["产品文档"]
        # 结构 facets 不被覆盖
        assert rep.facets["document"] == "onenet:DOC1:abc"


def test_document_facets_none_keeps_behavior():
    reps = project_representations(
        [_segment()], document_ref="doc-1", snapshot_ref="s1",
    )
    for rep in reps:
        assert "language" not in rep.facets
        assert rep.facets["document"] == "doc-1"


def test_document_facets_do_not_override_structural_keys():
    reps = project_representations(
        [_segment()],
        document_ref="doc-1", snapshot_ref="s1",
        # 恶意/冲突键：结构 facets 优先
        document_facets={"document": "fake", "content_type": "fake"},
    )
    for rep in reps:
        assert rep.facets["document"] == "doc-1"
        assert rep.facets["content_type"] != "fake"
