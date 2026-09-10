"""A2 章节身份按阅读序逐 segment 绑定（Codex 审查 P1-3 回归）.

缺陷回顾：``ref_of`` 按最终 ``by_path`` 映射查询——重开章节
（A → B → A）时第一个 A 的段落被错绑到最后一个 A 的 ref，
越界率=0 被破坏。修复后绑定必须在**构建期**按 segment 阅读序
记录（``refs_by_segment``），三方投影面（structure 节点 /
检索单元 section_ref / section 表示）同源消费。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment
from knowledge_mining.mining.retrieval_projection.projector import (
    project_representations,
)
from knowledge_mining.mining.retrieval_projection.section_identity import (
    build_section_identities,
)
from knowledge_mining.mining.retrieval_projection.structure_projection import (
    project_structure,
)


def _seg(i: int, chain: list[tuple[int, str]], text: str = "x") -> CompiledSegment:
    return CompiledSegment(
        segment_index=i, block_type="paragraph", raw_text=text,
        heading_chain=tuple(chain), token_count=1,
    )


# ---------------------------------------------------------------- per-segment 绑定


def test_reopened_section_segments_bind_in_reading_order():
    """A → B → A：早期正文绑第一个 A，后期正文绑第二个 A（各自 ref）."""
    segs = [
        _seg(0, [(1, "A")], "first-A"),
        _seg(1, [(1, "B")], "B-body"),
        _seg(2, [(1, "A")], "second-A"),
    ]
    idx = build_section_identities(segs, document_ref="doc.md")
    refs = idx.bound_ref(0), idx.bound_ref(1), idx.bound_ref(2)
    assert refs == (
        "doc.md#section:0", "doc.md#section:1", "doc.md#section:2",
    ), f"got {refs}"


def test_deep_reopen_binds_each_occurrence_separately():
    """A/X → B → A/X：早期 X 段落绑 0/0，后期绑 2/0."""
    segs = [
        _seg(0, [(1, "A"), (2, "X")], "early"),
        _seg(1, [(1, "B")], "B"),
        _seg(2, [(1, "A"), (2, "X")], "late"),
    ]
    idx = build_section_identities(segs, document_ref="doc.md")
    assert idx.bound_ref(0) == "doc.md#section:0/0"
    assert idx.bound_ref(2) == "doc.md#section:2/0"


def test_unheaded_segment_has_no_section_ref():
    segs = [_seg(0, [], "preamble"), _seg(1, [(1, "A")], "a")]
    idx = build_section_identities(segs, document_ref="doc.md")
    assert idx.bound_ref(0) is None
    assert idx.bound_ref(1) == "doc.md#section:0"


# ---------------------------------------------------------------- 投影面一致性


def test_prose_units_bind_by_reading_order():
    """project_representations 的 prose section_ref 必须按阅读序（非最终映射）."""
    segs = [
        _seg(0, [(1, "A")], "first-A"),
        _seg(1, [(1, "B")], "B-body"),
        _seg(2, [(1, "A")], "second-A"),
    ]
    reps = project_representations(
        segs, document_ref="doc.md", snapshot_ref="s1", include_sections=True,
    )
    by_text = {r.content_text: r for r in reps if r.representation_type == "prose"}
    assert by_text["first-A"].section_ref == "doc.md#section:0"
    assert by_text["B-body"].section_ref == "doc.md#section:1"
    assert by_text["second-A"].section_ref == "doc.md#section:2"


def test_section_representations_do_not_absorb_earlier_content():
    """重开的第二个 A 的 section 表示不得混入第一个 A 的正文."""
    segs = [
        _seg(0, [(1, "A")], "first-A"),
        _seg(1, [(1, "B")], "B-body"),
        _seg(2, [(1, "A")], "second-A"),
    ]
    reps = project_representations(
        segs, document_ref="doc.md", snapshot_ref="s1", include_sections=True,
    )
    sections = [r for r in reps if r.representation_type == "section"]
    by_ref = {r.target_ref: r for r in sections}
    # 两个 A 各自的 section 表示都存在
    assert "doc.md#section:0" in by_ref and "doc.md#section:2" in by_ref
    assert "first-A" in by_ref["doc.md#section:0"].content_text
    assert "second-A" in by_ref["doc.md#section:2"].content_text
    # 互不污染
    assert "second-A" not in by_ref["doc.md#section:0"].content_text
    assert "first-A" not in by_ref["doc.md#section:2"].content_text


def test_structure_segment_parent_follows_reading_order():
    """structure 投影的 segment parent_ref 与单元 section_ref 同源."""
    segs = [
        _seg(0, [(1, "A")], "first-A"),
        _seg(1, [(1, "B")], "B-body"),
        _seg(2, [(1, "A")], "second-A"),
    ]
    out = project_structure(segs, document_ref="doc.md")
    seg_nodes = {
        n["ref"]: n for n in out.nodes if n["node_type"] == "segment"
    }
    assert seg_nodes["doc.md#seg:0"]["parent_ref"] == "doc.md#section:0"
    assert seg_nodes["doc.md#seg:2"]["parent_ref"] == "doc.md#section:2"


def test_exact_scope_search_across_reopened_sections():
    """exact 搜索第一个 A 不得命中第二个 A 的正文（scope 下推正确性前提）."""
    segs = [
        _seg(0, [(1, "A")], "first-A-unique"),
        _seg(1, [(1, "B")], "B-body"),
        _seg(2, [(1, "A")], "second-A-other"),
    ]
    reps = project_representations(
        segs, document_ref="doc.md", snapshot_ref="s1",
    )
    in_first_a = [
        r for r in reps
        if r.representation_type == "prose" and r.section_ref == "doc.md#section:0"
    ]
    assert [r.content_text for r in in_first_a] == ["first-A-unique"]
