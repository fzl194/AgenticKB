"""默认 backend registry 构建（M2 WP4, SRS §C04 子集）.

注册 M2 压缩版的两个 legacy parser descriptor（Markdown / TXT）。
选择是确定性的：首个声明支持该 MIME 的 backend 胜出（M2 每格式至多
一个 backend；真正的规则路由是 WP6 / M3）。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parser_adapter import BackendRegistry
from knowledge_mining.mining.parse_adapters.legacy_markdown import (
    LegacyMarkdownParser,
)
from knowledge_mining.mining.parse_adapters.legacy_txt import (
    LegacyPlainTextParser,
)


def build_default_registry() -> BackendRegistry:
    """注册 legacy_markdown + legacy_txt 并返回 registry（SRS §C04）."""
    registry = BackendRegistry()
    registry.register(LegacyMarkdownParser().descriptor)
    registry.register(LegacyPlainTextParser().descriptor)
    return registry


__all__ = ["build_default_registry"]
