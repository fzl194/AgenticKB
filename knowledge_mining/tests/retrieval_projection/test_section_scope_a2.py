"""A2（34 号 P0-2/P0-3）：稳定章节身份 + 单元章节归属 + 同级顺序.

契约（39 号 §2.1）：
- section 身份 = 序号路径（``{doc}#section:{o1}/{o2}``），同名兄弟不碰撞，
  重开章节（同名标题二次出现）产生独立身份；
- section 节点携带 ordinal（同级顺序，prev/next 数据基础）与 element_id
  （opener 切片的标题元素锚——大纲↔st_ 桥，禁止标题文本匹配）；
- 检索单元携带 section_ref（其所属 section 节点 ref；document 级为 None；
  alias 继承源单元）；
- 无标题链的切片：section_ref=None（行为不回归）。
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


def _seg(i, block="paragraph", chain=(), text=None, element_ids=()):
    return CompiledSegment(
        segment_index=i,
        block_type=block,
        raw_text=text or f"text-{i}",
        heading_chain=tuple(chain),
        element_ids=tuple(element_ids),
    )


# ---------------------------------------------------------------------------
# section_identity：稳定身份
# ---------------------------------------------------------------------------


def _heading(element_id, level):
    from knowledge_mining.mining.contracts.parse_ir.types import Element
    return Element(element_id=element_id, element_type="heading", order_index=0,
                   style={"level": level})


def test_ordinal_path_refs_are_unique_for_distinguishable_sections():
    segs = [
        _seg(0, chain=[(1, "概述")], element_ids=("e1",)),
        _seg(1, chain=[(1, "参数")], element_ids=("e2",)),
        _seg(2, chain=[]),
    ]
    index = build_section_identities(segs, document_ref="doc.md",
        ir_elements=(_heading("e1", 1), _heading("e2", 1)))
    ids = index.identities
    refs = [s.ref for s in ids]
    assert refs == ["doc.md#section:0", "doc.md#section:1"]
    assert ids[0].ordinal == 0 and ids[1].ordinal == 1
    assert ids[0].element_id == "e1" and ids[1].element_id == "e2"
    # 深层章节：序号路径按层级拼接
    deep = build_section_identities(
        [
            _seg(0, chain=[(1, "A"), (2, "B")], element_ids=("e1",)),
            _seg(1, chain=[(1, "A"), (2, "C")], element_ids=("e2",)),
        ],
        document_ref="doc.md",
    ).identities
    assert [s.ref for s in deep] == [
        "doc.md#section:0", "doc.md#section:0/0", "doc.md#section:0/1",
    ]
    assert deep[1].parent_ref == "doc.md#section:0"
    assert deep[0].parent_ref == "doc.md#document"


def test_same_name_same_level_siblings_collapse_at_segment_layer():
    # 已知边界（39 号 §2.1）：同级同名兄弟在 CompiledSegment 层链元组完全
    # 相同（编译器标题栈本身折叠），投影层不引入标题文本匹配去拆分——
    # 该信息损失发生在编译器，修它需动 compiler 指纹（本轮明确不做）。
    segs = [
        _seg(0, chain=[(1, "概述")], element_ids=("e1",)),
        _seg(1, chain=[(1, "概述")], element_ids=("e2",)),
    ]
    ids = build_section_identities(segs, document_ref="doc.md").identities
    assert [s.ref for s in ids] == ["doc.md#section:0"]


def test_reopened_section_gets_fresh_identity():
    # 第3章 > 概述 ... 第4章 ... 第3章 > 概述（重复章标题）：第3章重开是
    # 全新身份；其子「概述」在新父下从序号 0 重新计。
    segs = [
        _seg(0, chain=[(1, "第3章"), (2, "概述")], element_ids=("h1", "c1")),
        _seg(1, chain=[(1, "第4章")], element_ids=("h2",)),
        _seg(2, chain=[(1, "第3章"), (2, "概述")], element_ids=("h3", "c3")),
    ]
    ids = build_section_identities(segs, document_ref="d", ir_elements=(
        _heading("h1", 1), _heading("c1", 2), _heading("h2", 1),
        _heading("h3", 1), _heading("c3", 2))).identities
    refs = [s.ref for s in ids]
    assert refs == [
        "d#section:0", "d#section:0/0", "d#section:1", "d#section:2",
        "d#section:2/0",
    ]
    assert ids[3].element_id == "h3" and ids[4].element_id is not None
    assert ids[4].parent_ref == "d#section:2"


def test_identity_without_opener_elements_degrades_to_none():
    ids = build_section_identities(
        [_seg(0, chain=[(1, "S")])], document_ref="d"
    ).identities
    assert ids[0].element_id is None


# ---------------------------------------------------------------------------
# projector：单元章节归属
# ---------------------------------------------------------------------------


def test_units_carry_section_ref():
    segs = [
        _seg(0, chain=[(1, "A"), (2, "B")], element_ids=("e1",)),
        _seg(1, chain=[(1, "A")], element_ids=("e2",)),
        _seg(2, chain=[]),
    ]
    reps = project_representations(segs, document_ref="d", snapshot_ref="snap")
    by_type = {}
    for rep in reps:
        by_type.setdefault(rep.representation_type, []).append(rep)
    # document 级无章节归属
    assert by_type["document"][0].section_ref is None
    # prose 归属其完整链的 section
    assert by_type["prose"][0].section_ref == "d#section:0/0"
    assert by_type["prose"][1].section_ref == "d#section:0"
    # 无链切片不归属
    assert by_type["prose"][2].section_ref is None


def test_section_representation_target_uses_identity_ref():
    segs = [
        _seg(0, chain=[(1, "A"), (2, "B")], element_ids=("e1",)),
        _seg(1, chain=[(1, "A"), (2, "B")], element_ids=("e2",)),
    ]
    reps = project_representations(
        segs, document_ref="d", snapshot_ref="snap", include_sections=True
    )
    sections = [r for r in reps if r.representation_type == "section"]
    assert len(sections) == 1
    assert sections[0].target_ref == "d#section:0/0"
    assert sections[0].section_ref == "d#section:0/0", "section 单元归属自身"


def test_table_row_unit_carries_section_ref():
    row = CompiledSegment(
        segment_index=0,
        block_type="table_row",
        raw_text="a=1",
        heading_chain=((1, "附录"),),
        element_ids=("t1",),
        metadata={"table_ref": "tbl:1", "table_header": ["a"], "row_index": 0,
                  "row_cells": [["a", "1", 0]]},
    )
    reps = project_representations((row,), document_ref="d", snapshot_ref="s")
    assert reps[-1].section_ref == "d#section:0"


# ---------------------------------------------------------------------------
# structure_projection：ordinal + element_id + 新 ref
# ---------------------------------------------------------------------------


def test_section_nodes_carry_ordinal_and_element_id():
    segs = [
        _seg(0, chain=[(1, "A")], element_ids=("h1",)),
        _seg(1, chain=[(1, "B")], element_ids=("h2",)),
        _seg(2, chain=[(1, "B"), (2, "C")], element_ids=("h3",)),
    ]
    structure = project_structure(segs, document_ref="d", ir_elements=(
        _heading("h1", 1), _heading("h2", 1), _heading("h3", 2)))
    sections = [n for n in structure.nodes if n["node_type"] == "section"]
    assert [n["ref"] for n in sections] == [
        "d#section:0", "d#section:1", "d#section:1/0",
    ]
    assert [n["ordinal"] for n in sections] == [0, 1, 0]
    assert [n["element_id"] for n in sections] == ["h1", "h2", "h3"]
    assert sections[2]["parent_ref"] == "d#section:1"
    # 表节点父级跟随新 ref
    row = CompiledSegment(
        segment_index=3, block_type="table_row", raw_text="x=1",
        heading_chain=((1, "B"), (2, "C")),
        metadata={"table_ref": "tbl:9", "table_header": ["x"], "row_index": 0,
                  "row_cells": [["x", "1", 0]]},
    )
    structure2 = project_structure(segs + [row], document_ref="d")
    table_node = next(n for n in structure2.nodes if n["node_type"] == "table")
    assert table_node["parent_ref"] == "d#section:1/0"


def test_edges_reference_new_section_refs():
    segs = [_seg(0, chain=[(1, "A")], element_ids=("h1",))]
    structure = project_structure(segs, document_ref="d")
    parent_edges = [e for e in structure.edges if e["relation"] == "parent"]
    assert any(
        e["from_ref"] == "d#section:0" and e["to_ref"] == "d#document"
        for e in parent_edges
    )


# ---------------------------------------------------------------------------
# alias 继承（范围搜索不得因别名召回漏掉范围内内容）
# ---------------------------------------------------------------------------


def test_alias_representation_inherits_section_ref():
    from knowledge_mining.mining.retrieval_projection.query_expansion import (
        _alias_representation,
    )

    base = project_representations(
        [_seg(0, chain=[(1, "A")], element_ids=("e1",))],
        document_ref="d", snapshot_ref="s",
    )[-1]
    alias = _alias_representation(base, "别名问题?", 0)
    assert alias.section_ref == base.section_ref == "d#section:0"


# ---------------------------------------------------------------------------
# 存量回填：plan_section_refs（标题路径口径，匹配旧快照已落库 ref）
# ---------------------------------------------------------------------------


def test_backfill_plan_uses_title_path_refs():
    from knowledge_mining.mining.retrieval_projection.section_backfill import (
        plan_section_refs,
    )

    # P1-2：真实列名 section_path（[{level, title}] JSON；[[l, t]] 兼容）
    segments = [
        {"segment_index": 0, "section_path": "[[1, \"A\"], [2, \"B\"]]"},
        {"segment_index": 1, "section_path": "[[1, \"A\"]]"},
        {"segment_index": 2, "section_path": None},
    ]
    units = [
        {"representation_id": "u-doc", "representation_type": "document",
         "target_ref": "d#document", "ordinal": -1, "provenance_json": "{}"},
        {"representation_id": "u-p0", "representation_type": "prose",
         "target_ref": "d#seg:0", "ordinal": 0, "provenance_json": "{}"},
        {"representation_id": "u-p1", "representation_type": "prose",
         "target_ref": "d#seg:1", "ordinal": 1, "provenance_json": "{}"},
        {"representation_id": "u-p2", "representation_type": "prose",
         "target_ref": "d#seg:2", "ordinal": 2, "provenance_json": "{}"},
        {"representation_id": "u-sec", "representation_type": "section",
         "target_ref": "d#section:A/B", "ordinal": 0, "provenance_json": "{}"},
        {"representation_id": "u-alias", "representation_type": "query_alias",
         "target_ref": "d#seg:0", "ordinal": 0,
         "provenance_json": "{\"source_representation\": \"u-p0\"}"},
    ]
    plan = plan_section_refs(
        segments=segments, units=units, document_ref="d"
    )
    assert plan == {
        "u-p0": "d#section:A/B",
        "u-p1": "d#section:A",
        "u-sec": "d#section:A/B",
        "u-alias": "d#section:A/B",  # alias 继承源单元
    }, plan
