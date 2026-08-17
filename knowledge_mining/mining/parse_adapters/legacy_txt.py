"""Legacy plain-text parser adapter（M2 WP4 压缩版, SRS §C06 / §4.6）.

要点：
- 按空行分段（思路同 ``stages/parse.py`` 的 ``_split_paragraphs``），每段
  一个 ``paragraph`` BackendBlock，行号为 0-based、end-exclusive。
- 刻意不引入 300-token 切分：M2 产出原子 element，一个长段就是一个
  paragraph element；切分是 Segment Compiler 的职责（SRS §3.7 "element
  不以 token 大小定义"）。
- 行区间精确到非空内容行：段尾随的空行不计入 line_end。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
    ParserAdapterError,
    ParserDescriptor,
    UnsupportedFormat,
)

LEGACY_TXT_PARSER_ID = "legacy_txt"
LEGACY_TXT_VERSION = "1.0.0"
LEGACY_TXT_FINGERPRINT = (
    f"{LEGACY_TXT_PARSER_ID}@{LEGACY_TXT_VERSION}#blankline-paragraphs"
)

LEGACY_TXT_MIMES = frozenset({"text/plain"})


class LegacyPlainTextParser:
    """DocumentParser 实现：纯文本按空行分段（SRS §C06）."""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=LEGACY_TXT_PARSER_ID,
            display_name="Legacy Plain-Text Parser",
            version=LEGACY_TXT_VERSION,
            supported_mimes=LEGACY_TXT_MIMES,
            backend_kind="local",
            parser_fingerprint=LEGACY_TXT_FINGERPRINT,
            capabilities=frozenset({"paragraphs"}),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{LEGACY_TXT_PARSER_ID} cannot parse mime {mime!r}"
            )
        text = _decode_utf8(data, LEGACY_TXT_PARSER_ID)
        blocks = tuple(
            BackendBlock(
                block_type="paragraph",
                text=para_text,
                line_start=line_start,
                line_end=line_end,
            )
            for para_text, line_start, line_end in _split_paragraphs(text)
        )
        return BackendParseArtifact(
            parser_id=LEGACY_TXT_PARSER_ID,
            parser_version=LEGACY_TXT_VERSION,
            mime=mime.lower(),
            blocks=blocks,
            raw_output=text,
        )


def _decode_utf8(data: bytes, parser_id: str) -> str:
    """严格 UTF-8 解码；坏字节包 ParserAdapterError（契约 v1.1，D-028）。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParserAdapterError(
            f"{parser_id}: source bytes are not valid UTF-8: {exc}"
        ) from exc


def _split_paragraphs(text: str) -> list[tuple[str, int, int]]:
    """按空行切分；返回 (段文本, line_start, line_end)，end-exclusive.

    段内保留原始行结构（join \\n）；连续空行视为一段分隔；段尾空行
    不计入 line_end，保证行区间精确指向非空内容。
    """
    lines = text.split("\n")
    paragraphs: list[tuple[str, int, int]] = []
    start: int | None = None
    last_content: int | None = None

    def _flush() -> None:
        nonlocal start, last_content
        if start is not None and last_content is not None:
            paragraphs.append((
                "\n".join(lines[start:last_content + 1]),
                start,
                last_content + 1,
            ))
        start = None
        last_content = None

    for idx, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = idx
            last_content = idx
        else:
            _flush()
    _flush()
    return paragraphs


__all__ = [
    "LEGACY_TXT_FINGERPRINT",
    "LEGACY_TXT_MIMES",
    "LEGACY_TXT_PARSER_ID",
    "LEGACY_TXT_VERSION",
    "LegacyPlainTextParser",
]
