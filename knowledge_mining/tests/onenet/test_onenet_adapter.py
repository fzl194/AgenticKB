# -*- coding: utf-8 -*-
"""onenet_jsonl 解析适配器：块产出 / normalizer 集成 / registry 路由 / 深层标题链."""
from __future__ import annotations

import json

import pytest

from knowledge_mining.mining.contracts.parser_adapter import UnsupportedFormat
from knowledge_mining.mining.parse_adapters.factory import (
    iter_native_parsers, resolve_pipeline,
)
from knowledge_mining.mining.parse_adapters.normalizer import LegacyLineNormalizer
from knowledge_mining.mining.parse_adapters.onenet_jsonl import (
    ONENET_JSONL_FINGERPRINT, ONENET_JSONL_MIME, OnenetJsonlParser,
)
from knowledge_mining.mining.parse_adapters.registry import build_default_registry

PKG = "Pkg.hwics"


def _jsonl(rows: list[dict]) -> bytes:
    return ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode()


def _row(part_id, path, content, nid=None):
    return {"nid": nid or f"n{part_id}", "part_id": part_id,
            "path": path, "title": path.split(" > ")[-1], "content": content}


# ------------------------------------------------------------- 块产出


def test_blocks_headings_only_on_new_path_levels():
    p = OnenetJsonlParser()
    art = p.parse(_jsonl([
        _row(1, f"{PKG} > L1 > L2 > 叶A", "第一段"),
        _row(2, f"{PKG} > L1 > L2 > 叶A", "第二段"),        # 同 path：不重复 heading
        _row(3, f"{PKG} > L1 > L2 > 叶A > 子节", "第三段"),  # 深一层：只补新层 heading
    ]), mime=ONENET_JSONL_MIME)
    kinds = [(b.block_type, b.text, b.level) for b in art.blocks]
    assert kinds == [
        ("heading", "Pkg.hwics", 1),   # beta-2：包段原样进标题链
        ("heading", "L1", 2),
        ("heading", "L2", 3),
        ("heading", "叶A", 4),
        ("paragraph", "第一段", None),
        ("paragraph", "第二段", None),
        ("heading", "子节", 5),
        ("paragraph", "第三段", None),
    ]


def test_blocks_table_extraction_with_structure():
    p = OnenetJsonlParser()
    art = p.parse(_jsonl([
        _row(1, f"{PKG} > A > B",
             "说明前文\n[tbl_predict_start]| 项目 | 说明 |\n| --- | --- |\n"
             "| MTU | 1500 |\n| VLAN | 4094 |[tbl_predict_end]\n结尾文字"),
    ]), mime=ONENET_JSONL_MIME)
    blocks = art.blocks
    assert [b.block_type for b in blocks] == [
        "heading", "heading", "heading", "paragraph", "table", "paragraph"]
    tbl = blocks[4]                    # beta-2：多一层包段 heading
    assert tbl.structure["columns"] == ["项目", "说明"]
    assert tbl.structure["rows"] == [
        {"项目": "MTU", "说明": "1500"},
        {"项目": "VLAN", "说明": "4094"},
    ]
    # 表格标记已从段落剥离
    assert all("[tbl_predict" not in (b.text or "") for b in blocks)


def test_blocks_native_ref_nid_part():
    p = OnenetJsonlParser()
    art = p.parse(_jsonl([_row(7, f"{PKG} > A", "x", nid="hwics_D_abc")]),
                  mime=ONENET_JSONL_MIME)
    para = [b for b in art.blocks if b.block_type == "paragraph"][0]
    assert para.native_ref == {"nid": "hwics_D_abc", "part_id": 7}


def test_bad_lines_counted_not_raised():
    p = OnenetJsonlParser()
    art = p.parse(b'{"nid":"n1","part_id":1,"path":"P > A","content":"ok"}\n'
                  b'not-json\n'
                  b'["list"]\n', mime=ONENET_JSONL_MIME)
    assert any("坏行跳过: 2" in w for w in art.warnings)
    assert any(b.block_type == "paragraph" for b in art.blocks)


def test_unsupported_mime_rejected():
    p = OnenetJsonlParser()
    with pytest.raises(UnsupportedFormat):
        p.parse(b"{}", mime="text/plain")


# ------------------------------------------------------------- normalizer 集成


def _normalize(art):
    return LegacyLineNormalizer(parser_fingerprints={
        "onenet_jsonl": ONENET_JSONL_FINGERPRINT}).normalize(
        art, source_raw_hash="h" * 64)


def test_normalizer_produces_ir_with_parent_chain():
    p = OnenetJsonlParser()
    art = p.parse(_jsonl([
        _row(1, f"{PKG} > L1 > L2 > 叶A", "正文A"),
        _row(2, f"{PKG} > L1 > L2 > 叶B", "正文B"),
    ]), mime=ONENET_JSONL_MIME)
    doc = _normalize(art)
    elements = doc.elements
    ids_by_text = {}
    for e in elements:
        ids_by_text.setdefault(e.text, []).append(e.element_id)
    l1, l2 = ids_by_text["L1"][0], ids_by_text["L2"][0]
    leaf_a, leaf_b = ids_by_text["叶A"][0], ids_by_text["叶B"][0]
    # parent_of：source=parent, target=child（normalizer 弹栈链）
    parent_of = {
        (r.source_element_id, r.target_element_id)
        for r in doc.relations if r.relation_type == "parent_of"
    }
    assert (l1, l2) in parent_of        # L1 → L2
    assert (l2, leaf_a) in parent_of    # L2 → 叶A
    assert (l2, leaf_b) in parent_of    # L2 → 叶B


def test_normalizer_table_asset_and_validation_pass():
    p = OnenetJsonlParser()
    art = p.parse(_jsonl([
        _row(1, f"{PKG} > A > B",
             "[tbl_predict_start]| 项目 | 值 |\n| --- | --- |\n| a | 1 |[tbl_predict_end]"),
    ]), mime=ONENET_JSONL_MIME)
    doc = _normalize(art)
    tables = list(doc.structured_assets.values())
    assert tables, "应有 TableAsset"
    tbl = tables[0]
    assert tbl.rows == 2 and tbl.columns == 2
    cells = {(c.row_index, c.column_index): c.text for c in tbl.cells}
    assert cells[(0, 0)] == "项目" and cells[(1, 1)] == "1"


def test_normalizer_deep_heading_chain_10_levels():
    p = OnenetJsonlParser()
    deep = " > ".join(f"L{i}" for i in range(1, 11))
    art = p.parse(_jsonl([_row(1, f"{PKG} > {deep}", "深文")]),
                  mime=ONENET_JSONL_MIME)
    doc = _normalize(art)
    headings = [e for e in doc.elements if e.element_type == "heading"]
    assert len(headings) == 11          # beta-2：包段 + L1..L10
    levels = sorted(h.style.get("level") for h in headings)
    assert levels == list(range(1, 12))


def test_heading_chain_merges_spanning_title():
    """标题含 > ：heading 链输出单层「告警 > 处理建议」，不产假层级."""
    rows = [
        {"nid": "a", "part_id": 1, "path": "包 > 接口管理 > 告警 > 处理建议",
         "title": "告警 > 处理建议", "content": "正文A"},
        {"nid": "b", "part_id": 2, "path": "包 > 接口管理 > 定位思路",
         "title": "定位思路", "content": "正文B"},
    ]
    art = OnenetJsonlParser().parse(_jsonl(rows), mime=ONENET_JSONL_MIME)
    headings = [b.text for b in art.blocks if b.block_type == "heading"]
    assert headings == ["包", "接口管理", "告警 > 处理建议", "定位思路"]


def test_heading_chain_inherits_spanning_parent_title_for_descendant():
    rows = [
        {"nid": "parent", "part_id": 1,
         "path": "包 > 告警 > 处理建议", "title": "告警 > 处理建议",
         "content": "父正文"},
        {"nid": "child", "part_id": 2,
         "path": "包 > 告警 > 处理建议 > 操作步骤", "title": "操作步骤",
         "content": "子正文"},
    ]
    art = OnenetJsonlParser().parse(_jsonl(rows), mime=ONENET_JSONL_MIME)
    headings = [(b.text, b.level) for b in art.blocks if b.block_type == "heading"]
    assert headings == [("包", 1), ("告警 > 处理建议", 2), ("操作步骤", 3)]


def test_heading_chain_uses_stored_segments_for_filtered_descendant_batch():
    from knowledge_mining.mining.onenet.restore import ONENET_PATH_SEGMENTS_FIELD

    row = {
        "nid": "child", "part_id": 2,
        "path": "包 > 告警 > 处理建议 > 操作步骤", "title": "操作步骤",
        ONENET_PATH_SEGMENTS_FIELD: ["包", "告警 > 处理建议", "操作步骤"],
        "content": "子正文",
    }
    art = OnenetJsonlParser().parse(_jsonl([row]), mime=ONENET_JSONL_MIME)
    headings = [(b.text, b.level) for b in art.blocks if b.block_type == "heading"]
    assert headings == [("包", 1), ("告警 > 处理建议", 2), ("操作步骤", 3)]


def test_fingerprint_version_bumped():
    from knowledge_mining.mining.parse_adapters.onenet_jsonl import (
        ONENET_JSONL_FINGERPRINT, ONENET_JSONL_VERSION,
    )
    assert ONENET_JSONL_VERSION == "1.1.2"
    assert ONENET_JSONL_FINGERPRINT.startswith("onenet_jsonl@1.1.2")


# ------------------------------------------------------------- registry 路由


def test_factory_pipeline_resolvable():
    pair = resolve_pipeline("onenet_jsonl")
    assert pair is not None
    parser, normalizer = pair
    assert isinstance(parser, OnenetJsonlParser)


def test_registry_routes_onenet_mime_to_onenet_parser():
    registry = build_default_registry()
    descriptor = registry.select_for(ONENET_JSONL_MIME)
    assert descriptor is not None
    assert descriptor.parser_id == "onenet_jsonl"
    # legacy_txt 不得抢路由专用 mime
    txt = next(d for d in registry.all() if d.parser_id == "legacy_txt")
    assert not txt.supports(ONENET_JSONL_MIME)


def test_default_plan_factory_picks_onenet():
    from knowledge_mining.mining.workflow.new_chain_services import (
        DocumentParseFacade,
    )
    from types import SimpleNamespace
    raw = SimpleNamespace(mime=ONENET_JSONL_MIME)
    plan = DocumentParseFacade.default_plan_factory(raw, {})
    assert plan.primary_parser_id == "onenet_jsonl"
