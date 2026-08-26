"""PDF 文本层 fallback adapter（P02-S3）。"""
from __future__ import annotations

from io import BytesIO

from pdfminer.high_level import extract_text

from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock, BackendParseArtifact, ParserAdapterError, ParserDescriptor,
    UnsupportedFormat,
)

PDF_TEXT_LAYER_PARSER_ID = "pdf_text_layer"
PDF_TEXT_LAYER_VERSION = "1.0.0"
PDF_TEXT_LAYER_FINGERPRINT = f"{PDF_TEXT_LAYER_PARSER_ID}@{PDF_TEXT_LAYER_VERSION}#pdfminer"
PDF_TEXT_LAYER_MIMES = frozenset({"application/pdf"})


class PdfTextLayerParser:
    """仅提取 PDF 可用文本层；以内容完整性换取版式/表格结构。"""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=PDF_TEXT_LAYER_PARSER_ID,
            display_name="PDF text-layer fallback (pdfminer)",
            version=PDF_TEXT_LAYER_VERSION,
            supported_mimes=PDF_TEXT_LAYER_MIMES,
            backend_kind="local",
            parser_fingerprint=PDF_TEXT_LAYER_FINGERPRINT,
            capabilities=frozenset({"pages", "text_layer", "fallback"}),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(f"{PDF_TEXT_LAYER_PARSER_ID} cannot parse {mime!r}")
        try:
            text = extract_text(BytesIO(data))
        except Exception as exc:  # pdfminer third-party errors stay at adapter boundary
            raise ParserAdapterError(f"pdf text-layer extraction failed: {exc}") from exc
        blocks = tuple(
            BackendBlock(
                block_type="paragraph", text=page.strip(),
                container_ref={"container_type": "page", "index": index},
            )
            for index, page in enumerate(text.split("\f")) if page.strip()
        )
        return BackendParseArtifact(
            parser_id=PDF_TEXT_LAYER_PARSER_ID,
            parser_version=PDF_TEXT_LAYER_VERSION,
            mime=mime.lower(), blocks=blocks, raw_output=text,
        )


__all__ = ["PDF_TEXT_LAYER_PARSER_ID", "PdfTextLayerParser"]
