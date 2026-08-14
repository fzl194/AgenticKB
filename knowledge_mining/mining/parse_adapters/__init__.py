"""Legacy Parser Adapters（M2 WP4 压缩版：Markdown / TXT, SRS §C06/§C07）.

适配层职责边界（SRS §4.6）：
- 只做 "一次解析执行 -> BackendParseArtifact -> Parse IR"；
- 不写业务表、不选择切分策略、不发布 Build、不静默 fallback；
- 依赖方向：parse_adapters -> contracts，绝不反向（ADR-0003 D-001）。

旧链路（ingestion/、stages/、workflow/）保持不动，硬隔离。
"""
from __future__ import annotations

from knowledge_mining.mining.parse_adapters.legacy_markdown import (
    LEGACY_MARKDOWN_FINGERPRINT,
    LEGACY_MARKDOWN_MIMES,
    LEGACY_MARKDOWN_PARSER_ID,
    LEGACY_MARKDOWN_VERSION,
    LegacyMarkdownParser,
)
from knowledge_mining.mining.parse_adapters.legacy_txt import (
    LEGACY_TXT_FINGERPRINT,
    LEGACY_TXT_MIMES,
    LEGACY_TXT_PARSER_ID,
    LEGACY_TXT_VERSION,
    LegacyPlainTextParser,
)
from knowledge_mining.mining.parse_adapters.normalizer import (
    DOC_CONTAINER_ID,
    NORMALIZER_VERSION,
    LegacyLineNormalizer,
)
from knowledge_mining.mining.parse_adapters.registry import (
    build_default_registry,
)

__all__ = [
    # markdown adapter
    "LEGACY_MARKDOWN_FINGERPRINT",
    "LEGACY_MARKDOWN_MIMES",
    "LEGACY_MARKDOWN_PARSER_ID",
    "LEGACY_MARKDOWN_VERSION",
    "LegacyMarkdownParser",
    # txt adapter
    "LEGACY_TXT_FINGERPRINT",
    "LEGACY_TXT_MIMES",
    "LEGACY_TXT_PARSER_ID",
    "LEGACY_TXT_VERSION",
    "LegacyPlainTextParser",
    # normalizer
    "DOC_CONTAINER_ID",
    "NORMALIZER_VERSION",
    "LegacyLineNormalizer",
    # registry
    "build_default_registry",
]
