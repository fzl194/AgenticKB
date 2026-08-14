"""Unit tests for ``LegacyMarkdownParser``（M2 WP4, SRS §C06/§4.6）.

纯逻辑测试，无 IO / async。覆盖：标题层级、段落、列表展开为逐条
list_item、代码块、pipe 表格行列还原、quote；每 block 的
line_start/line_end 精确（0-based, end-exclusive）；不支持的 MIME。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.contracts.parser_adapter import UnsupportedFormat
from knowledge_mining.mining.parse_adapters.legacy_markdown import (
    LegacyMarkdownParser,
)

MD_SAMPLE = (
    "# Title\n"
    "\n"
    "Intro paragraph here.\n"
    "\n"
    "## Sub A\n"
    "\n"
    "- alpha\n"
    "- beta\n"
    "  - beta.1\n"
    "\n"
    "```python\n"
    "x = 1\n"
    "```\n"
    "\n"
    "> quoted line\n"
    "\n"
    "| a | b |\n"
    "|---|---|\n"
    "| 1 | 2 |\n"
)


@pytest.fixture
def parser() -> LegacyMarkdownParser:
    return LegacyMarkdownParser()


def test_supports_mimes(parser: LegacyMarkdownParser) -> None:
    assert parser.supports("text/markdown")
    assert parser.supports("text/x-markdown")
    assert parser.supports("TEXT/MARKDOWN")  # 大小写归一
    assert not parser.supports("text/plain")
    assert not parser.supports("application/pdf")


def test_parse_rejects_unsupported_mime(parser: LegacyMarkdownParser) -> None:
    with pytest.raises(UnsupportedFormat):
        parser.parse("hello", mime="text/plain")


def test_parse_block_sequence_and_line_ranges(
    parser: LegacyMarkdownParser,
) -> None:
    artifact = parser.parse(MD_SAMPLE, mime="text/markdown")

    assert artifact.parser_id == "legacy_markdown"
    assert artifact.mime == "text/markdown"
    assert artifact.raw_output == MD_SAMPLE

    summary = [(b.block_type, b.line_start, b.line_end) for b in artifact.blocks]
    assert summary == [
        # heading：line_end 修正为 line_start+1（heading_close 无 map）
        ("heading", 0, 1),
        ("paragraph", 2, 3),
        ("heading", 4, 5),
        # list 展开为逐条 list_item，行号逐项精确
        ("list_item", 6, 7),
        ("list_item", 7, 8),
        ("list_item", 8, 9),
        ("code", 10, 13),
        ("quote", 14, 15),
        ("table", 16, 19),
    ]


def test_heading_levels_and_text(parser: LegacyMarkdownParser) -> None:
    artifact = parser.parse(MD_SAMPLE, mime="text/markdown")
    headings = [b for b in artifact.blocks if b.block_type == "heading"]
    assert [(b.level, b.text) for b in headings] == [(1, "Title"), (2, "Sub A")]


def test_multi_level_headings(parser: LegacyMarkdownParser) -> None:
    text = "# H1\n\n## H2\n\n### H3\n\ntext\n"
    artifact = parser.parse(text, mime="text/markdown")
    headings = [b for b in artifact.blocks if b.block_type == "heading"]
    assert [b.level for b in headings] == [1, 2, 3]
    assert [(b.line_start, b.line_end) for b in headings] == [
        (0, 1), (2, 3), (4, 5),
    ]
    para = [b for b in artifact.blocks if b.block_type == "paragraph"][0]
    assert (para.line_start, para.line_end) == (6, 7)


def test_list_items_carry_depth_and_text(parser: LegacyMarkdownParser) -> None:
    artifact = parser.parse(MD_SAMPLE, mime="text/markdown")
    items = [b for b in artifact.blocks if b.block_type == "list_item"]
    assert [(b.level, b.text) for b in items] == [
        (1, "alpha"),
        (1, "beta"),
        (2, "beta.1"),
    ]


def test_ordered_list_expands_to_items(parser: LegacyMarkdownParser) -> None:
    text = "1. first\n2. second\n3. third\n"
    artifact = parser.parse(text, mime="text/markdown")
    items = [b for b in artifact.blocks if b.block_type == "list_item"]
    assert [b.text for b in items] == ["first", "second", "third"]
    assert [(b.line_start, b.line_end) for b in items] == [
        (0, 1), (1, 2), (2, 3),
    ]


def test_code_block_language_and_content(parser: LegacyMarkdownParser) -> None:
    artifact = parser.parse(MD_SAMPLE, mime="text/markdown")
    code = [b for b in artifact.blocks if b.block_type == "code"][0]
    assert code.text == "x = 1\n"
    assert code.structure["language"] == "python"
    assert (code.line_start, code.line_end) == (10, 13)


def test_quote_block(parser: LegacyMarkdownParser) -> None:
    artifact = parser.parse(MD_SAMPLE, mime="text/markdown")
    quote = [b for b in artifact.blocks if b.block_type == "quote"][0]
    assert quote.text == "quoted line"
    assert (quote.line_start, quote.line_end) == (14, 15)


def test_table_structure_roundtrip(parser: LegacyMarkdownParser) -> None:
    artifact = parser.parse(MD_SAMPLE, mime="text/markdown")
    table = [b for b in artifact.blocks if b.block_type == "table"][0]
    assert (table.line_start, table.line_end) == (16, 19)
    assert table.structure["columns"] == ["a", "b"]
    assert table.structure["rows"] == [{"a": "1", "b": "2"}]
    assert table.structure["row_count"] == 1
    assert table.structure["col_count"] == 2


def test_descriptor_identity(parser: LegacyMarkdownParser) -> None:
    d = parser.descriptor
    assert d.parser_id == "legacy_markdown"
    assert d.parser_fingerprint.startswith("legacy_markdown@1.0.0#")
    assert "text/markdown" in d.supported_mimes
