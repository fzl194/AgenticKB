"""Unit tests for ``LegacyPlainTextParser``（M2 WP4, SRS §C06/§4.6）.

纯逻辑测试。覆盖：多段落切分、连续空行、无空行的单段长文本（必须是
1 个 element，绝不 token 切分——切分是 Segment Compiler 的职责，
SRS §3.7）、行号精确（0-based, end-exclusive）、不支持的 MIME。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.contracts.parser_adapter import UnsupportedFormat
from knowledge_mining.mining.parse_adapters.legacy_txt import (
    LegacyPlainTextParser,
)


@pytest.fixture
def parser() -> LegacyPlainTextParser:
    return LegacyPlainTextParser()


def test_supports_plain_text_only(parser: LegacyPlainTextParser) -> None:
    assert parser.supports("text/plain")
    assert not parser.supports("text/markdown")
    assert not parser.supports("application/pdf")


def test_parse_rejects_markdown_mime(parser: LegacyPlainTextParser) -> None:
    with pytest.raises(UnsupportedFormat):
        parser.parse("# nope", mime="text/markdown")


def test_multiple_paragraphs_with_exact_lines(
    parser: LegacyPlainTextParser,
) -> None:
    text = "aaa\nbbb\n\n\ncmp\n"
    artifact = parser.parse(text, mime="text/plain")

    assert artifact.parser_id == "legacy_txt"
    assert artifact.raw_output == text
    summary = [(b.block_type, b.text, b.line_start, b.line_end) for b in artifact.blocks]
    # 连续空行只算一个分隔；line_end 精确指向非空内容之后
    assert summary == [
        ("paragraph", "aaa\nbbb", 0, 2),
        ("paragraph", "cmp", 4, 5),
    ]


def test_paragraph_keeps_internal_lines(parser: LegacyPlainTextParser) -> None:
    text = "line one\nline two\nline three\n\nsecond para\n"
    artifact = parser.parse(text, mime="text/plain")
    first = artifact.blocks[0]
    assert first.text == "line one\nline two\nline three"
    assert (first.line_start, first.line_end) == (0, 3)
    second = artifact.blocks[1]
    assert (second.line_start, second.line_end) == (4, 5)


def test_single_long_paragraph_never_token_split(
    parser: LegacyPlainTextParser,
) -> None:
    # 无空行的长文本：必须仍是 1 个 element，不得引入 token 切分
    long_line = "word " * 2000
    text = long_line + "\n" + long_line + "\n" + long_line
    artifact = parser.parse(text, mime="text/plain")
    assert len(artifact.blocks) == 1
    block = artifact.blocks[0]
    assert block.block_type == "paragraph"
    assert block.text == text
    assert (block.line_start, block.line_end) == (0, 3)


def test_leading_and_trailing_blank_lines(parser: LegacyPlainTextParser) -> None:
    text = "\n\n\nbody\n\n\n"
    artifact = parser.parse(text, mime="text/plain")
    assert len(artifact.blocks) == 1
    block = artifact.blocks[0]
    assert block.text == "body"
    assert (block.line_start, block.line_end) == (3, 4)


def test_empty_text_yields_no_blocks(parser: LegacyPlainTextParser) -> None:
    artifact = parser.parse("\n\n\n", mime="text/plain")
    assert artifact.blocks == ()


def test_descriptor_identity(parser: LegacyPlainTextParser) -> None:
    d = parser.descriptor
    assert d.parser_id == "legacy_txt"
    assert d.parser_fingerprint == "legacy_txt@1.0.0#blankline-paragraphs"
    assert d.supported_mimes == frozenset({"text/plain"})
