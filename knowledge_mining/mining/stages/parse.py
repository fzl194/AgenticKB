"""Document parser interface and factory for v1.1.

Dispatches by file_type:
- markdown -> MarkdownParser (structural chunking via markdown-it-py)
- txt -> PlainTextParser (paragraph-based chunking)
- pdf -> PdfParser (structural extraction via pdfminer.six layout API)
- others -> PassthroughParser (no segments)
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from knowledge_mining.mining.contracts.models import ContentBlock, SectionNode
from knowledge_mining.mining.infra.text_utils import token_count as _token_count
from knowledge_mining.mining.infra.structure import parse_structure as _parse_md_structure
from knowledge_mining.mining.infra.pdf_parser import parse_pdf_to_section_tree
from knowledge_mining.mining.infra.docx_parser import parse_docx_to_section_tree

logger = logging.getLogger(__name__)


class ParserStage:
    """Stage wrapper for the parser factory."""
    stage_name = "parse"
    stage_version = "1"

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    def execute(self, context: dict[str, Any], **kw: Any) -> dict[str, Any]:
        raw = context.get("raw_file")
        if raw is None:
            return context
        parser = create_parser(raw.file_type, **self._kwargs)
        if parser is None:
            return context
        parse_ctx: dict[str, Any] = {"file_path": raw.file_path}
        if context.get("image_dir"):
            parse_ctx["image_dir"] = context["image_dir"]
        elif context.get("run_id") and getattr(raw, "file_type", None) == "pdf":
            from knowledge_mining.mining.infra.run_workdir import resolve_run_image_dir

            doc_key = getattr(raw, "relative_path", None) or raw.file_name
            parse_ctx["image_dir"] = str(
                resolve_run_image_dir(str(context["run_id"]), str(doc_key))
            )
        tree = parser.parse(raw.content, raw.file_name, parse_ctx)
        context["tree"] = tree
        return context


@runtime_checkable
class DocumentParser(Protocol):
    def parse(
        self, content: str, file_name: str, context: dict[str, Any],
    ) -> SectionNode | None: ...


class MarkdownParser:
    """Structural parser for Markdown using markdown-it-py."""

    def parse(
        self, content: str, file_name: str, context: dict[str, Any],
    ) -> SectionNode | None:
        if not content.strip():
            return None
        return _parse_md_structure(content)


class PlainTextParser:
    """Paragraph-based chunking for plain text."""

    def __init__(self, chunk_size: int = 300, chunk_overlap: int = 30):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def parse(
        self, content: str, file_name: str, context: dict[str, Any],
    ) -> SectionNode | None:
        if not content.strip():
            return None

        paragraphs = _split_paragraphs(content)
        if not paragraphs:
            return None

        blocks: list[ContentBlock] = []
        for para_text, line_start, line_end in paragraphs:
            tc = _token_count(para_text)
            if tc <= self.chunk_size:
                blocks.append(ContentBlock(
                    block_type="paragraph", text=para_text,
                    line_start=line_start, line_end=line_end,
                ))
            else:
                chunks = _split_long_text(para_text, self.chunk_size, self.chunk_overlap)
                for chunk in chunks:
                    blocks.append(ContentBlock(
                        block_type="paragraph", text=chunk,
                        line_start=line_start, line_end=line_end,
                    ))

        return SectionNode(title=file_name, level=0, blocks=tuple(blocks))


class PassthroughParser:
    """Parser for non-parsable file types. Returns None."""

    def parse(
        self, content: str, file_name: str, context: dict[str, Any],
    ) -> SectionNode | None:
        return None


class PdfParser:
    """Structural parser for PDF using pdfminer.six layout API."""

    def __init__(self) -> None:
        # 解析失败时不抛异常（文档记为"跳过"而非"失败"），但把原因留在这里，
        # 供 parse_stage 取走写进 run document —— 否则用户只能看到笼统的"跳过"。
        self.last_error: str | None = None

    def parse(
        self, content: str, file_name: str, context: dict[str, Any],
    ) -> SectionNode | None:
        file_path = (context or {}).get("file_path")
        if not file_path:
            logger.warning("PdfParser: no file_path in context for %s", file_name)
            self.last_error = "no file_path in parse context"
            return None
        image_dir = (context or {}).get("image_dir")
        try:
            return parse_pdf_to_section_tree(
                file_path, doc_title=file_name, image_dir=image_dir,
            )
        except Exception as e:
            logger.warning("PdfParser failed for %s: %s", file_path, e)
            self.last_error = f"{type(e).__name__}: {e}"
            return None


class DocxParser:
    """Structural parser for DOCX using python-docx."""

    def __init__(self) -> None:
        # 同 PdfParser：失败不抛，原因留给 parse_stage 取走。
        self.last_error: str | None = None

    def parse(
        self, content: str, file_name: str, context: dict[str, Any],
    ) -> SectionNode | None:
        file_path = (context or {}).get("file_path")
        if not file_path:
            logger.warning("DocxParser: no file_path in context for %s", file_name)
            self.last_error = "no file_path in parse context"
            return None
        try:
            return parse_docx_to_section_tree(file_path, doc_title=file_name)
        except Exception as e:
            logger.warning("DocxParser failed for %s: %s", file_path, e)
            self.last_error = f"{type(e).__name__}: {e}"
            return None


def create_parser(file_type: str, **kwargs: Any) -> DocumentParser:
    """Factory: return appropriate parser for the given file_type."""
    if file_type == "markdown":
        return MarkdownParser()
    elif file_type == "txt":
        return PlainTextParser(
            chunk_size=kwargs.get("chunk_size", 300),
            chunk_overlap=kwargs.get("chunk_overlap", 30),
        )
    elif file_type == "pdf":
        return PdfParser()
    elif file_type == "docx":
        return DocxParser()
    else:
        return PassthroughParser()


def _split_paragraphs(text: str) -> list[tuple[str, int, int]]:
    """Split text by blank lines into paragraphs with line numbers."""
    paragraphs: list[tuple[str, int, int]] = []
    current_lines: list[str] = []
    line_start: int | None = None
    lines = text.split("\n")
    for line_idx, line in enumerate(lines):
        if line.strip() == "":
            if current_lines:
                paragraphs.append(("\n".join(current_lines), line_start, line_idx))
                current_lines = []
                line_start = None
        else:
            if line_start is None:
                line_start = line_idx
            current_lines.append(line)
    if current_lines:
        paragraphs.append(("\n".join(current_lines), line_start, len(lines)))
    return paragraphs


def _find_token_boundaries(text: str) -> list[int]:
    """Find character positions of token boundaries in text."""
    boundaries: list[int] = []
    buf_start: int | None = None
    for i, ch in enumerate(text):
        if "\u4e00" <= ch <= "\u9fff":
            if buf_start is not None:
                boundaries.append(buf_start)
                buf_start = None
            boundaries.append(i)
        elif ch.isalnum():
            if buf_start is None:
                buf_start = i
        else:
            if buf_start is not None:
                boundaries.append(buf_start)
                buf_start = None
    if buf_start is not None:
        boundaries.append(buf_start)
    return boundaries


def _split_long_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split long text into chunks based on token boundaries."""
    boundaries = _find_token_boundaries(text)
    total_tokens = len(boundaries)

    if total_tokens <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < total_tokens:
        end = min(start + chunk_size, total_tokens)
        char_start = boundaries[start]
        char_end = boundaries[end] if end < total_tokens else len(text)
        chunk = text[char_start:char_end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= total_tokens:
            break
        step = chunk_size - chunk_overlap
        start += step

    return chunks
