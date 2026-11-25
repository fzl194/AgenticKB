"""S2 · native_json 解析器闸门（文件格式支持补全，68号报告 §六.S2）。

Seam：
- ``NativeJsonParser.parse``（application/json bytes → heading/paragraph/
  table blocks，LegacyLineNormalizer 建 IR）；
- 接线四对账点：safe_intake、inspect、router、factory/registry。
真实 json 文件端到端验收不在本文件。
"""
from __future__ import annotations

import json

import pytest

from knowledge_mining.mining.contracts.parser_adapter import (
    ParserAdapterError, UnsupportedFormat,
)
from knowledge_mining.mining.frozen_input.safe_intake import SafeIntake
from knowledge_mining.mining.file_inspector.inspect import FileInspector
from knowledge_mining.mining.file_inspector.router import ParserRouter
from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline
from knowledge_mining.mining.parse_adapters.native.native_json import (
    NATIVE_JSON_PARSER_ID, NativeJsonParser,
)
from knowledge_mining.mining.parse_adapters.registry import (
    build_default_registry,
)

SAMPLE_OBJ = {
    "设备": {
        "名称": "景兰大桥",
        "测点数": 42,
        "启用": True,
    },
    "月度记录": [
        {"月份": "1月", "处理量": 12},
        {"月份": "2月", "处理量": 7},
    ],
}


def _parse(payload, mime="application/json"):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return NativeJsonParser().parse(data, mime=mime)


def test_object_maps_to_headings_paragraphs_and_table():
    artifact = _parse(SAMPLE_OBJ)
    headings = [b for b in artifact.blocks if b.block_type == "heading"]
    paragraphs = [b for b in artifact.blocks if b.block_type == "paragraph"]
    tables = [b for b in artifact.blocks if b.block_type == "table"]

    assert [h.text for h in headings] == ["设备", "月度记录"]
    assert {h.level for h in headings} == {1}
    # 标量叶子成段：数值/布尔照实转写
    texts = {p.text for p in paragraphs}
    assert "名称: 景兰大桥" in texts
    assert "测点数: 42" in texts
    assert "启用: true" in texts
    # 对象数组成表
    assert len(tables) == 1
    assert tables[0].structure["columns"] == ["月份", "处理量"]
    assert tables[0].structure["rows"][0] == {"月份": "1月", "处理量": "12"}


def test_nested_objects_produce_deeper_heading_levels():
    artifact = _parse({"a": {"b": {"c": 1}}})
    headings = [b for b in artifact.blocks if b.block_type == "heading"]
    assert [h.text for h in headings] == ["a", "b"]
    assert [h.level for h in headings] == [1, 2]
    # 叶子标量是段落（结构才是标题）
    paragraphs = [b for b in artifact.blocks if b.block_type == "paragraph"]
    assert paragraphs[0].text == "c: 1"


def test_top_level_array_of_objects_maps_to_table_without_heading():
    artifact = _parse([{"x": 1, "y": 2}, {"x": 3, "y": 4}])
    assert [b.block_type for b in artifact.blocks] == ["table"]
    assert artifact.blocks[0].structure["rows"][1] == {"x": "3", "y": "4"}


def test_scalar_values_render_stably():
    artifact = _parse({"n": 1.5, "s": "文本", "b": False, "nul": None})
    texts = {p.text for p in artifact.blocks if p.block_type == "paragraph"}
    assert "n: 1.5" in texts
    assert "s: 文本" in texts
    assert "b: false" in texts
    assert "nul: null" in texts


def test_invalid_json_raises_adapter_error():
    with pytest.raises(ParserAdapterError):
        NativeJsonParser().parse(b"{not json", mime="application/json")


def test_rejects_wrong_mime():
    with pytest.raises(UnsupportedFormat):
        NativeJsonParser().parse(b"{}", mime="text/plain")


def test_element_cap_truncates_with_warning():
    payload = {f"k{i}": i for i in range(1_100)}
    artifact = _parse(payload)
    assert any("truncated" in w for w in artifact.warnings)
    assert len(artifact.blocks) < 1_100


def test_pipeline_and_chain_wiring():
    pair = resolve_pipeline(NATIVE_JSON_PARSER_ID)
    assert pair is not None and isinstance(pair[0], NativeJsonParser)
    registry = build_default_registry()
    assert any(
        d.parser_id == NATIVE_JSON_PARSER_ID and d.license_status == "ok"
        for d in registry.all() if d.supports("application/json")
    )
    # safe_intake / inspect / router
    data = json.dumps(SAMPLE_OBJ, ensure_ascii=False).encode()
    verdict = SafeIntake().inspect(data, "conf.json")
    assert verdict.ok is True and verdict.detected_mime == "application/json"
    profile = FileInspector().inspect(data, declared_mime="application/json")
    assert profile.source_format == "json"
    decision = ParserRouter(build_default_registry()).plan(profile)
    assert decision.primary_parser_id == NATIVE_JSON_PARSER_ID
