"""S2 · native_csv 解析器闸门（文件格式支持补全，68号报告 §六.S2）。

Seam：
- ``NativeCsvParser.parse``（text/csv bytes → 单 table block，
  structure={columns, rows}，LegacyLineNormalizer 建表资产）；
- 接线四对账点：safe_intake（text/csv 白名单+检测）、inspect（csv 标签）、
  router（csv→native_csv）、factory/registry。
真实 csv 文件的上传→挖掘→检索端到端验收不在本文件（仓库根流程）。
"""
from __future__ import annotations

from knowledge_mining.mining.frozen_input.safe_intake import SafeIntake
from knowledge_mining.mining.file_inspector.inspect import FileInspector
from knowledge_mining.mining.file_inspector.router import ParserRouter
from knowledge_mining.mining.parse_adapters.factory import (
    iter_native_parsers, resolve_pipeline,
)
from knowledge_mining.mining.parse_adapters.native.native_csv import (
    NATIVE_CSV_FINGERPRINT, NATIVE_CSV_MIMES, NATIVE_CSV_PARSER_ID,
    NativeCsvParser,
)
from knowledge_mining.mining.parse_adapters.registry import (
    build_default_registry,
)

SAMPLE = "月份,处理量(万篇),通过率\n1月,12,0.97\n2月,7,0.96\n".encode("utf-8")


def test_csv_parses_to_single_table_block():
    artifact = NativeCsvParser().parse(SAMPLE, mime="text/csv")
    assert artifact.parser_id == NATIVE_CSV_PARSER_ID
    tables = [b for b in artifact.blocks if b.block_type == "table"]
    assert len(tables) == 1
    structure = tables[0].structure
    assert structure["columns"] == ["月份", "处理量(万篇)", "通过率"]
    assert structure["rows"][0] == {"月份": "1月", "处理量(万篇)": "12", "通过率": "0.97"}


def test_csv_rejects_wrong_mime():
    import pytest
    from knowledge_mining.mining.contracts.parser_adapter import UnsupportedFormat
    with pytest.raises(UnsupportedFormat):
        NativeCsvParser().parse(SAMPLE, mime="text/plain")


def test_csv_decodes_gbk():
    gbk = "月份,处理量\n1月,12\n".encode("gbk")
    artifact = NativeCsvParser().parse(gbk, mime="text/csv")
    structure = artifact.blocks[0].structure
    assert structure["columns"] == ["月份", "处理量"]


def test_csv_decodes_utf8_bom():
    bom = b"\xef\xbb\xbf" + SAMPLE
    artifact = NativeCsvParser().parse(bom, mime="text/csv")
    assert artifact.blocks[0].structure["columns"][0] == "月份"


def test_csv_empty_rows_yields_no_blocks():
    artifact = NativeCsvParser().parse(b"\n\n", mime="text/csv")
    assert artifact.blocks == ()
    assert any("empty" in w for w in artifact.warnings)


def test_csv_row_cap_truncates_with_warning():
    rows = "\n".join(["id,v"] + [f"{i},{i}" for i in range(50_100)])
    artifact = NativeCsvParser().parse(rows.encode(), mime="text/csv")
    assert any("truncated" in w for w in artifact.warnings)
    assert len(artifact.blocks[0].structure["rows"]) < 50_100


def test_csv_sniffs_semicolon_delimiter():
    artifact = NativeCsvParser().parse("a;b\n1;2\n".encode(), mime="text/csv")
    assert artifact.blocks[0].structure["columns"] == ["a", "b"]


def test_descriptor_and_pipeline_wiring():
    pair = resolve_pipeline(NATIVE_CSV_PARSER_ID)
    assert pair is not None
    parser, normalizer = pair
    assert isinstance(parser, NativeCsvParser)
    # registry：descriptor 注册且 license ok（router 可选中）
    registry = build_default_registry()
    matches = [d for d in registry.all() if d.supports("text/csv")]
    assert any(d.parser_id == NATIVE_CSV_PARSER_ID and d.license_status == "ok"
               for d in matches)
    # iter_native_parsers 覆盖（default_plan_factory 依赖同表）
    assert any(p.descriptor.parser_id == NATIVE_CSV_PARSER_ID
               for p in iter_native_parsers())
    assert NATIVE_CSV_MIMES == frozenset({"text/csv"})
    assert NATIVE_CSV_PARSER_ID in NATIVE_CSV_FINGERPRINT


def test_intake_inspect_router_chain_for_csv(tmp_path):
    # safe_intake：.csv 文本无签名 → text/csv（而非 text/plain）
    verdict = SafeIntake().inspect(SAMPLE, "stats.csv")
    assert verdict.ok is True
    assert verdict.detected_mime == "text/csv"
    # inspect：csv 标签
    profile = FileInspector().inspect(SAMPLE, declared_mime="text/csv")
    assert profile.source_format == "csv"
    # router：native_csv 为主解析器
    decision = ParserRouter(build_default_registry()).plan(profile)
    assert decision.primary_parser_id == NATIVE_CSV_PARSER_ID
