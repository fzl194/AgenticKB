"""按 MIME/画像解析 parser + normalizer 成对流水线（M3.5 集成层）.

ShadowParseService 构造时需要**成对**注入 (DocumentParser, ParseIRNormalizer)。
本模块把 Router 决策落到具体实现：

```text
FileInspector.inspect(bytes) -> DocumentProfile
ParserRouter.plan(profile)    -> RouteDecision(primary_parser_id, ...)
resolve_pipeline(decision)    -> (parser, normalizer) | None
```

每个 parser_id 绑定其专属 normalizer（映射逻辑与后端一一对应），工厂
是唯一知道"实现类 ↔ descriptor"对应关系的地方——调用方（workflow 接线、
e2e、未来 M4 Orchestrator）不感知具体类。

References: SRS §C04（Registry）、§C05（Router）、§C06（Adapter）；
ADR-0003 D-028A（M3 纯代码混合路线）。
"""
from __future__ import annotations

from typing import Callable

from knowledge_mining.mining.contracts.parser_adapter import (
    DocumentParser,
    ParseIRNormalizer,
)
from knowledge_mining.mining.parse_adapters.legacy_markdown import (
    LEGACY_MARKDOWN_PARSER_ID,
    LegacyMarkdownParser,
)
from knowledge_mining.mining.parse_adapters.legacy_txt import (
    LEGACY_TXT_PARSER_ID,
    LegacyPlainTextParser,
)
from knowledge_mining.mining.parse_adapters.native.native_docx import (
    NATIVE_DOCX_PARSER_ID,
    DocxNormalizer,
    NativeDocxParser,
)
from knowledge_mining.mining.parse_adapters.native.native_html import (
    NATIVE_HTML_PARSER_ID,
    HtmlNormalizer,
    NativeHtmlParser,
)
from knowledge_mining.mining.parse_adapters.native.native_pptx import (
    NATIVE_PPTX_PARSER_ID,
    NativePptxParser,
    PptxNormalizer,
)
from knowledge_mining.mining.parse_adapters.native.native_xlsx import (
    NATIVE_XLSX_PARSER_ID,
    NativeXlsxParser,
    XlsxNormalizer,
)
from knowledge_mining.mining.parse_adapters.native_pdf import (
    NATIVE_PDF_PARSER_ID,
    NativePdfParser,
)
from knowledge_mining.mining.parse_adapters.normalizer import LegacyLineNormalizer
from knowledge_mining.mining.parse_adapters.pdf_normalizer import PdfNormalizer

#: parser_id -> (parser 工厂, normalizer 工厂)。占位槽位（docling/cloud_vlm）
#: 不在此表——它们没有实现，Router 也不会选中（license_status != "ok"）。
_PIPELINE_FACTORIES: dict[
    str, tuple[Callable[[], DocumentParser], Callable[[], ParseIRNormalizer]]
] = {
    LEGACY_MARKDOWN_PARSER_ID: (LegacyMarkdownParser, LegacyLineNormalizer),
    LEGACY_TXT_PARSER_ID: (LegacyPlainTextParser, LegacyLineNormalizer),
    NATIVE_DOCX_PARSER_ID: (NativeDocxParser, DocxNormalizer),
    NATIVE_XLSX_PARSER_ID: (NativeXlsxParser, XlsxNormalizer),
    NATIVE_PPTX_PARSER_ID: (NativePptxParser, PptxNormalizer),
    NATIVE_HTML_PARSER_ID: (NativeHtmlParser, HtmlNormalizer),
    NATIVE_PDF_PARSER_ID: (NativePdfParser, PdfNormalizer),
}


def iter_native_parsers() -> list[DocumentParser]:
    """实例化全部**已实现**的 parser（注册进 registry 用，M3.5）."""
    return [factory() for factory, _ in _PIPELINE_FACTORIES.values()]


def resolve_pipeline(
    parser_id: str,
) -> tuple[DocumentParser, ParseIRNormalizer] | None:
    """按 parser_id 返回成对 (parser, normalizer)；未知 id 返回 None."""
    pair = _PIPELINE_FACTORIES.get(parser_id)
    if pair is None:
        return None
    parser_factory, normalizer_factory = pair
    return parser_factory(), normalizer_factory()


__all__ = ["iter_native_parsers", "resolve_pipeline"]
