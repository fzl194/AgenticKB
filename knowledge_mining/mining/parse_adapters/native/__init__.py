"""M3 原生格式解析适配器子包（SRS §C06/§C07, ADR-0003 D-028A）.

工业级成熟库 -> BackendBlock 映射 + Normalizer（库输出 -> Parse IR），
不写任何解析算法：
- DOCX  -> python-docx（``native_docx``）
- XLSX  -> openpyxl（``native_xlsx``，公式/展示值双读）
- PPTX  -> python-pptx（``native_pptx``，EMU 坐标）
- HTML  -> lxml（``native_html``，xpath 证据）

公共 Normalizer 骨架在 ``_base.BaseNativeNormalizer``；M2 的
``LegacyLineNormalizer`` 不受本包影响。
"""
from __future__ import annotations

from knowledge_mining.mining.parse_adapters.native._base import (
    BaseNativeNormalizer,
)
from knowledge_mining.mining.parse_adapters.native.native_docx import (
    DOCX_SECTION_CONTAINER_ID,
    NATIVE_DOCX_FINGERPRINT,
    NATIVE_DOCX_MIMES,
    NATIVE_DOCX_PARSER_ID,
    NATIVE_DOCX_VERSION,
    DocxNormalizer,
    NativeDocxParser,
)
from knowledge_mining.mining.parse_adapters.native.native_html import (
    DOM_CONTAINER_ID,
    NATIVE_HTML_FINGERPRINT,
    NATIVE_HTML_MIMES,
    NATIVE_HTML_PARSER_ID,
    NATIVE_HTML_VERSION,
    HtmlNormalizer,
    NativeHtmlParser,
)
from knowledge_mining.mining.parse_adapters.native.native_pptx import (
    NATIVE_PPTX_FINGERPRINT,
    NATIVE_PPTX_MIMES,
    NATIVE_PPTX_PARSER_ID,
    NATIVE_PPTX_VERSION,
    NativePptxParser,
    PptxNormalizer,
)
from knowledge_mining.mining.parse_adapters.native.native_xlsx import (
    NATIVE_XLSX_FINGERPRINT,
    NATIVE_XLSX_MIMES,
    NATIVE_XLSX_PARSER_ID,
    NATIVE_XLSX_VERSION,
    NativeXlsxParser,
    WORKBOOK_CONTAINER_ID,
    XlsxNormalizer,
)

__all__ = [
    # 公共骨架
    "BaseNativeNormalizer",
    # DOCX
    "DOCX_SECTION_CONTAINER_ID",
    "NATIVE_DOCX_FINGERPRINT",
    "NATIVE_DOCX_MIMES",
    "NATIVE_DOCX_PARSER_ID",
    "NATIVE_DOCX_VERSION",
    "DocxNormalizer",
    "NativeDocxParser",
    # XLSX
    "NATIVE_XLSX_FINGERPRINT",
    "NATIVE_XLSX_MIMES",
    "NATIVE_XLSX_PARSER_ID",
    "NATIVE_XLSX_VERSION",
    "WORKBOOK_CONTAINER_ID",
    "NativeXlsxParser",
    "XlsxNormalizer",
    # PPTX
    "NATIVE_PPTX_FINGERPRINT",
    "NATIVE_PPTX_MIMES",
    "NATIVE_PPTX_PARSER_ID",
    "NATIVE_PPTX_VERSION",
    "NativePptxParser",
    "PptxNormalizer",
    # HTML
    "DOM_CONTAINER_ID",
    "NATIVE_HTML_FINGERPRINT",
    "NATIVE_HTML_MIMES",
    "NATIVE_HTML_PARSER_ID",
    "NATIVE_HTML_VERSION",
    "HtmlNormalizer",
    "NativeHtmlParser",
]
