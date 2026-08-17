"""Unit tests for ``LegacyLineNormalizer``（M2 WP4, SRS §C07/§4.7）.

纯逻辑测试。覆盖：element 类型映射、heading parent 链（h1→h2→h3 嵌套）、
next_in_reading_order 完整性、EvidenceSpan 行可回溯（给 element 能找回
原文件行文本）、TableAsset 行列/表头、stable id 同输入两次一致、
to_dict/from_dict round-trip 后再 validate、TXT 全 paragraph 无伪造
heading、悬空 parent_id 的 validator 生效性、默认 registry。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.contracts.parse_ir import (
    PARSE_IR_SCHEMA_VERSION,
    Container,
    Element,
    EvidenceSpan,
    ParseIdentity,
    ParsedDocument,
    validate,
)
from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
)
from knowledge_mining.mining.parse_adapters.legacy_markdown import (
    LegacyMarkdownParser,
)
from knowledge_mining.mining.parse_adapters.legacy_txt import (
    LegacyPlainTextParser,
)
from knowledge_mining.mining.parse_adapters.normalizer import (
    NORMALIZER_VERSION,
    LegacyLineNormalizer,
)
from knowledge_mining.mining.parse_adapters.registry import (


    build_default_registry,
)


def _b(s: str) -> bytes:
    """契约 v1.1：parse 输入统一 bytes（文本格式由适配器解码）。"""
    return s.encode("utf-8")



MD_SAMPLE = (
    "# Title\n"        # 0
    "\n"               # 1
    "Intro.\n"         # 2
    "\n"               # 3
    "## Sub A\n"       # 4
    "\n"               # 5
    "- alpha\n"        # 6
    "\n"               # 7
    "### Deep\n"       # 8
    "\n"               # 9
    "Deep text.\n"     # 10
    "\n"               # 11
    "## Sub B\n"       # 12
    "\n"               # 13
    "| a | b |\n"      # 14
    "|---|---|\n"      # 15
    "| 1 | 2 |\n"      # 16
)

RAW_HASH = "ab" * 32


@pytest.fixture
def normalizer() -> LegacyLineNormalizer:
    return LegacyLineNormalizer()


def _md_doc(normalizer: LegacyLineNormalizer) -> ParsedDocument:
    artifact = LegacyMarkdownParser().parse(_b(MD_SAMPLE), mime="text/markdown")
    return normalizer.normalize(artifact, source_raw_hash=RAW_HASH)


# ---------------------------------------------------------------------------
# Markdown -> Parse IR
# ---------------------------------------------------------------------------

def test_element_types_in_reading_order(normalizer: LegacyLineNormalizer) -> None:
    doc = _md_doc(normalizer)
    assert [e.element_type for e in doc.elements] == [
        "heading", "paragraph", "heading", "list_item",
        "heading", "paragraph", "heading", "table",
    ]
    assert [e.order_index for e in doc.elements] == list(range(8))


def test_heading_parent_chain_h1_h2_h3(normalizer: LegacyLineNormalizer) -> None:
    doc = _md_doc(normalizer)
    by_text = {e.text: e for e in doc.elements}

    assert by_text["Title"].parent_id is None          # h1 无父
    assert by_text["Intro."].parent_id == by_text["Title"].element_id
    # h2 -> h1；列表项挂到最近标题 Sub A 下
    assert by_text["Sub A"].parent_id == by_text["Title"].element_id
    assert by_text["alpha"].parent_id == by_text["Sub A"].element_id
    # h3 -> h2（最近的上一个更浅 level 标题）
    assert by_text["Deep"].parent_id == by_text["Sub A"].element_id
    assert by_text["Deep text."].parent_id == by_text["Deep"].element_id
    # 回到第二个 h2：父回到 h1
    assert by_text["Sub B"].parent_id == by_text["Title"].element_id
    assert by_text[doc.elements[-1].text].parent_id == by_text["Sub B"].element_id


def test_next_in_reading_order_complete(normalizer: LegacyLineNormalizer) -> None:
    doc = _md_doc(normalizer)
    chain = [
        r for r in doc.relations if r.relation_type == "next_in_reading_order"
    ]
    assert len(chain) == len(doc.elements) - 1
    for prev, cur in zip(doc.elements, doc.elements[1:]):
        assert any(
            r.source_element_id == prev.element_id
            and r.target_element_id == cur.element_id
            for r in chain
        )
    # parent_of 数量 = 有 parent 的元素数
    parents = [e for e in doc.elements if e.parent_id is not None]
    assert len([r for r in doc.relations if r.relation_type == "parent_of"]) == len(parents)


def test_evidence_span_line_traceback(normalizer: LegacyLineNormalizer) -> None:
    doc = _md_doc(normalizer)
    lines = MD_SAMPLE.split("\n")

    for element in doc.elements:
        assert len(element.source_spans) == 1
        span = element.source_spans[0]
        assert span.text_range == (0, len(element.text))
        locator = span.source_locator
        assert locator is not None
        # 行可回溯：用 span 行区间从原文件切片 == raw_text
        recovered = "\n".join(lines[locator["line_start"]:locator["line_end"]])
        assert recovered == span.raw_text

    heading = doc.elements[0]
    assert heading.source_spans[0].raw_text == "# Title"
    assert heading.source_spans[0].source_locator == {"line_start": 0, "line_end": 1}
    table_span = doc.elements[-1].source_spans[0]
    assert table_span.source_locator == {"line_start": 14, "line_end": 17}


def test_table_asset_rows_columns_header(normalizer: LegacyLineNormalizer) -> None:
    doc = _md_doc(normalizer)
    assert len(doc.structured_assets) == 1
    asset = next(iter(doc.structured_assets.values()))
    table_element = doc.elements[-1]
    assert asset.table_id == f"{table_element.element_id}-table"
    assert (asset.rows, asset.columns) == (2, 2)  # 表头行 + 1 数据行
    assert asset.header_regions == ((0, 0),)

    header = [c for c in asset.cells if c.is_header]
    assert {(c.text) for c in header} == {"a", "b"}
    data = [c for c in asset.cells if not c.is_header]
    assert {(c.row_index, c.column_index, c.text) for c in data} == {
        (1, 0, "1"), (1, 1, "2"),
    }


def test_single_section_container_no_fake_pages(
    normalizer: LegacyLineNormalizer,
) -> None:
    doc = _md_doc(normalizer)
    assert len(doc.containers) == 1
    container = doc.containers[0]
    assert container.container_type == "section"  # MD/TXT 无页容器
    assert container.page_number is None          # 不伪造页码


def test_identity_and_diagnostics(normalizer: LegacyLineNormalizer) -> None:
    doc = _md_doc(normalizer)
    identity = doc.source_identity
    assert identity.source_raw_hash == RAW_HASH
    assert identity.parser_fingerprint.startswith("legacy_markdown@")
    assert identity.normalizer_version == NORMALIZER_VERSION
    assert doc.parse_run_id is None

    diag = doc.diagnostics
    assert diag.parser_name == "legacy_markdown"
    assert diag.parser_version is not None
    assert doc.schema_version == PARSE_IR_SCHEMA_VERSION


def test_parse_run_id_propagates(normalizer: LegacyLineNormalizer) -> None:
    artifact = LegacyMarkdownParser().parse(_b(MD_SAMPLE), mime="text/markdown")
    doc = normalizer.normalize(
        artifact, source_raw_hash=RAW_HASH, parse_run_id="run-123"
    )
    assert doc.parse_run_id == "run-123"


def test_stable_ids_same_input_twice(normalizer: LegacyLineNormalizer) -> None:
    doc1 = _md_doc(normalizer)
    doc2 = _md_doc(normalizer)
    assert [e.element_id for e in doc1.elements] == [e.element_id for e in doc2.elements]
    assert doc1 == doc2


def test_normalized_text_is_minimal_strip(normalizer: LegacyLineNormalizer) -> None:
    doc = _md_doc(normalizer)
    for element in doc.elements:
        assert element.normalized_text == element.text.strip()


def test_roundtrip_dict_then_validate(normalizer: LegacyLineNormalizer) -> None:
    doc = _md_doc(normalizer)
    as_dict = doc.to_dict()
    restored = ParsedDocument.from_dict(as_dict)

    assert restored == doc
    result = validate(restored)
    assert result.valid, [i.message for i in result.issues]


def test_unmapped_block_type_falls_to_unknown_with_warning(
    normalizer: LegacyLineNormalizer,
) -> None:
    artifact = BackendParseArtifact(
        parser_id="legacy_markdown",
        parser_version="1.0.0",
        mime="text/markdown",
        blocks=(BackendBlock("weird_block", "mystery", 0, 1),),
        raw_output="mystery\n",
    )
    doc = normalizer.normalize(artifact, source_raw_hash=RAW_HASH)
    assert doc.elements[0].element_type == "unknown"
    assert any("weird_block" in w for w in doc.diagnostics.warnings)
    assert validate(doc).valid


# ---------------------------------------------------------------------------
# TXT -> Parse IR
# ---------------------------------------------------------------------------

def test_txt_all_paragraphs_no_fake_headings(normalizer: LegacyLineNormalizer) -> None:
    text = "first para\nwith two lines\n\nsecond para\n" * 1
    artifact = LegacyPlainTextParser().parse(_b(text), mime="text/plain")
    doc = normalizer.normalize(artifact, source_raw_hash=RAW_HASH)

    assert all(e.element_type == "paragraph" for e in doc.elements)
    assert all(e.parent_id is None for e in doc.elements)  # 无伪造 heading 层级
    assert not doc.structured_assets
    assert doc.source_identity.parser_fingerprint.startswith("legacy_txt@")
    assert doc.diagnostics.parser_name == "legacy_txt"
    assert validate(doc).valid

    span = doc.elements[0].source_spans[0]
    assert span.source_locator == {"line_start": 0, "line_end": 2}
    assert span.raw_text == "first para\nwith two lines"


# ---------------------------------------------------------------------------
# Validator 生效性（手工构造异常 IR）
# ---------------------------------------------------------------------------

def test_validator_flags_dangling_parent_id() -> None:
    # 悬空 parent_id：手工构造 ParsedDocument，validate 必须报 error
    bad_doc = ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=ParseIdentity(
            source_raw_hash="x",
            parser_fingerprint="legacy_markdown@1.0.0#test",
            normalizer_version=NORMALIZER_VERSION,
        ),
        containers=(Container("c-doc", "section", 0),),
        elements=(Element(
            element_id="e-ghost-child",
            element_type="paragraph",
            order_index=0,
            text="t",
            normalized_text="t",
            parent_id="no-such-element",
            source_spans=(EvidenceSpan("s0", text_range=(0, 1)),),
        ),),
    )
    result = validate(bad_doc)
    assert not result.valid
    assert any(issue.code == "dangling_parent" for issue in result.issues)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_default_registry_selects_backends_by_mime() -> None:
    registry = build_default_registry()
    md = registry.select_for("text/markdown")
    txt = registry.select_for("text/plain")
    assert md is not None and md.parser_id == "legacy_markdown"
    assert txt is not None and txt.parser_id == "legacy_txt"
    # M3 云端槽位（SRS §C04）：docling/cloud_vlm 占位已注册；select_for
    # 不做许可过滤，但占位符 license_status != "ok"，不可被 Router 选为
    # primary（路由层过滤见 tests/parse_adapters/test_registry.py）。
    # M3.5：PDF 现在由已实现的 native_pdf 承接（license ok），
    # 占位槽位 docling/cloud_vlm 仍在但 license != "ok"。
    pdf_slot = registry.select_for("application/pdf")
    by_id = {d.parser_id: d for d in registry.all()}
    assert pdf_slot is not None and pdf_slot.parser_id == "native_pdf"
    assert by_id["docling"].license_status != "ok"
    assert by_id["cloud_vlm"].license_status != "ok"
    assert len(registry.all()) == 9
