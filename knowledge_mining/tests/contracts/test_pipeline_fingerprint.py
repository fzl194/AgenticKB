"""契约层 v1.2 测试：规则配置指纹 + artifact 序列化 + effective fingerprint.

用户指令（2026-08-17 整改轮）：
- parser、规则配置、依赖和 normalizer 任一变化都会改变 effective pipeline
  fingerprint（SRS §3.5 Parse Artifact 身份指纹的落地）。
- Office/PDF/HTML 的 backend raw artifact 可持久化和 replay（§9.5/A09）
  ——先决条件是 ``BackendParseArtifact`` 可 JSON round-trip。
- bbox 统一为 ``(x0, top, x1, bottom)``（§7.4 visual_region），validator
  必须拒绝乱序 bbox。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.contracts.parse_ir import validate
from knowledge_mining.mining.contracts.parse_ir.types import (
    Element,
    EvidenceSpan,
    ParsedDocument,
    ParseIdentity,
)
from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
)


# ---------------------------------------------------------------------------
# ParseRuleConfig
# ---------------------------------------------------------------------------


def test_rule_config_defaults_exist_and_are_frozen():
    from knowledge_mining.mining.contracts.parser_adapter import ParseRuleConfig

    cfg = ParseRuleConfig()
    assert cfg.heading_size_ratio == pytest.approx(1.15)
    with pytest.raises(Exception):
        cfg.heading_size_ratio = 2.0  # frozen


def test_rule_config_fingerprint_stable_and_sensitive():
    from knowledge_mining.mining.contracts.parser_adapter import ParseRuleConfig

    a = ParseRuleConfig()
    b = ParseRuleConfig()
    assert a.config_fingerprint() == b.config_fingerprint()

    changed = ParseRuleConfig(heading_size_ratio=1.30)
    assert changed.config_fingerprint() != a.config_fingerprint()

    other = ParseRuleConfig(line_top_tolerance=9.0)
    assert other.config_fingerprint() != a.config_fingerprint()


# ---------------------------------------------------------------------------
# BackendParseArtifact round-trip（持久化 + replay 的先决条件）
# ---------------------------------------------------------------------------


def _sample_artifact() -> BackendParseArtifact:
    return BackendParseArtifact(
        parser_id="native_pdf",
        parser_version="1.0.0",
        mime="application/pdf",
        blocks=(
            BackendBlock(
                block_type="heading",
                text="标题",
                level=2,
                container_ref={"container_type": "page", "index": 3},
                bbox=(72.0, 700.0, 280.0, 716.0),
                native_ref={"page": 3},
                structure={"heading_rule": "font_size_ratio"},
            ),
            BackendBlock(block_type="paragraph", text="正文", line_start=4, line_end=5),
        ),
        raw_output="",
        warnings=("w1",),
        errors=(),
        usage={"pages": 2},
    )


def test_backend_artifact_round_trips_through_json():
    import json

    art = _sample_artifact()
    data = json.loads(json.dumps(art.to_dict(), ensure_ascii=False))
    restored = BackendParseArtifact.from_dict(data)

    assert restored.parser_id == art.parser_id
    assert restored.mime == art.mime
    assert len(restored.blocks) == 2
    b0 = restored.blocks[0]
    assert b0.bbox == (72.0, 700.0, 280.0, 716.0)  # tuple 而非 list
    assert b0.container_ref == {"container_type": "page", "index": 3}
    assert b0.structure == {"heading_rule": "font_size_ratio"}
    assert restored.warnings == ("w1",)
    assert restored.usage == {"pages": 2}


# ---------------------------------------------------------------------------
# effective pipeline fingerprint
# ---------------------------------------------------------------------------


def test_effective_fingerprint_changes_with_each_component():
    from knowledge_mining.mining.contracts.parser_adapter import (
        effective_pipeline_fingerprint,
    )

    base = dict(
        parser_fingerprint="native_pdf@1.0.0#pdfplumber-0.11.4",
        normalizer_version="pdf-native@1",
        rule_config_fingerprint="abc",
        dependency_fingerprint="dep-1",
    )
    fp0 = effective_pipeline_fingerprint(**base)
    assert effective_pipeline_fingerprint(**base) == fp0  # 确定性

    varied = dict(base)
    varied["parser_fingerprint"] = "native_pdf@1.1.0#pdfplumber-0.11.4"
    assert effective_pipeline_fingerprint(**varied) != fp0

    varied = dict(base)
    varied["normalizer_version"] = "pdf-native@2"
    assert effective_pipeline_fingerprint(**varied) != fp0

    varied = dict(base)
    varied["rule_config_fingerprint"] = "zzz"
    assert effective_pipeline_fingerprint(**varied) != fp0

    varied = dict(base)
    varied["dependency_fingerprint"] = "dep-2"
    assert effective_pipeline_fingerprint(**varied) != fp0


# ---------------------------------------------------------------------------
# ParseIdentity 扩展：rule_config_fingerprint 字段
# ---------------------------------------------------------------------------


def test_parse_identity_accepts_rule_config_fingerprint():
    ident = ParseIdentity(
        source_raw_hash="h",
        parser_fingerprint="p",
        rule_config_fingerprint="rc",
    )
    assert ident.rule_config_fingerprint == "rc"
    # 默认值 None（未知不伪造）
    assert ParseIdentity(source_raw_hash="h", parser_fingerprint="p").rule_config_fingerprint is None


def test_parse_identity_round_trip_keeps_rule_config_fingerprint():
    ident = ParseIdentity(
        source_raw_hash="h",
        parser_fingerprint="p",
        rule_config_fingerprint="rc",
        reconciler_version="reconciler@1",
    )
    doc = ParsedDocument(
        schema_version=ident.parse_ir_schema_version,
        source_identity=ident,
        containers=(),
        elements=(),
    )
    restored = ParsedDocument.from_dict(doc.to_dict())
    assert restored.source_identity.rule_config_fingerprint == "rc"
    assert restored.source_identity.reconciler_version == "reconciler@1"


# ---------------------------------------------------------------------------
# validator：bbox 顺序（x0 <= x1, top <= bottom）
# ---------------------------------------------------------------------------


def _doc_with_bbox(bbox: list[float]) -> ParsedDocument:
    return ParsedDocument(
        schema_version="0.1",
        source_identity=ParseIdentity(source_raw_hash="h", parser_fingerprint="p"),
        containers=(),
        elements=(
            Element(
                element_id="e0",
                element_type="paragraph",
                order_index=0,
                text="t",
                source_spans=(
                    EvidenceSpan(
                        span_id="e0-s0",
                        visual_region={"bbox": bbox, "unit": "pt"},
                    ),
                ),
            ),
        ),
    )


def test_validator_rejects_inverted_bbox():
    result = validate(_doc_with_bbox([280.0, 700.0, 72.0, 716.0]))
    codes = [i.code for i in result.issues if i.level == "error"]
    assert "invalid_bbox_order" in codes
    assert not result.valid


def test_validator_rejects_inverted_vertical_bbox():
    result = validate(_doc_with_bbox([72.0, 716.0, 280.0, 700.0]))
    codes = [i.code for i in result.issues if i.level == "error"]
    assert "invalid_bbox_order" in codes


def test_validator_accepts_well_formed_bbox():
    result = validate(_doc_with_bbox([72.0, 700.0, 280.0, 716.0]))
    assert result.valid
