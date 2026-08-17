"""Legacy Markdown parser adapter（M2 WP4 压缩版, SRS §C06 / §4.6）.

要点：
- 复用现有 markdown 结构解析 ``mining/infra/structure.py`` 的
  ``_tokens_to_blocks``（``parse_structure`` 的 token->ContentBlock 转换
  步骤，markdown-it-py），得到带行号的扁平 block 序列（SRS §4.6
  BackendParseArtifact.blocks）。
- 不走 ``parse_structure`` 的 SectionNode 树：树会把 heading 吸收为
  节点 title 而丢掉 heading 块与行号；token 级拍平保留全部块与行区间。
- list block 展开为逐条 ``list_item`` BackendBlock（level=嵌套深度），
  行号按源文本逐行回扫配对；配对数量不一致时整体回退为一个 list
  block，不伪造行号（SRS §7.4 "未知可缺，不得伪造"）。
- heading 的 line_end 修正为 line_start+1：markdown-it 的
  heading_close 无 map，token 转换里 line_end 会退化为 line_start。
- ``parse`` 是同步纯函数（无 IO），raw_output 保留解码后的原文，
  便于后续仅升级 Normalizer 重放（SRS §9.5 replay）。
"""
from __future__ import annotations

import re
from typing import Any

import markdown_it
from markdown_it import MarkdownIt

from knowledge_mining.mining.contracts.models import ContentBlock
from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
    ParserAdapterError,
    ParserDescriptor,
    UnsupportedFormat,
)
from knowledge_mining.mining.parse_adapters.legacy_txt import _decode_utf8
from knowledge_mining.mining.infra.structure import _tokens_to_blocks

# --- 身份与指纹（SRS §C04 descriptor / §3.5 parser_fingerprint） -------------
# 版本号是模块内常量；# 后是结构解析逻辑标识（infra.structure 主版本 +
# markdown-it-py 版本），任一变化都应改变指纹，从而触发重新解析。
LEGACY_MARKDOWN_PARSER_ID = "legacy_markdown"
LEGACY_MARKDOWN_VERSION = "1.0.0"
_STRUCTURE_LOGIC_ID = f"infra.structure@1+markdown-it-py-{markdown_it.__version__}"
LEGACY_MARKDOWN_FINGERPRINT = (
    f"{LEGACY_MARKDOWN_PARSER_ID}@{LEGACY_MARKDOWN_VERSION}#{_STRUCTURE_LOGIC_ID}"
)

LEGACY_MARKDOWN_MIMES = frozenset({"text/markdown", "text/x-markdown"})

# ContentBlock.block_type -> BackendBlock.block_type（词表对齐，仅改名）。
_BLOCK_TYPE_NAMES = {
    "heading": "heading",
    "paragraph": "paragraph",
    "list": "list",
    "list_item": "list_item",
    "code": "code",
    "blockquote": "quote",
    "table": "table",
    "html_table": "html_table",
    "raw_html": "raw_html",
    "image": "image",
}

# 列表项起始行（无序列表符号 / 有序列表序号），用于逐项回扫行号。
_LIST_ITEM_LINE_RE = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])\s+")


class LegacyMarkdownParser:
    """DocumentParser 实现：包装既有 markdown 结构解析（SRS §C06）."""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=LEGACY_MARKDOWN_PARSER_ID,
            display_name="Legacy Markdown Parser",
            version=LEGACY_MARKDOWN_VERSION,
            supported_mimes=LEGACY_MARKDOWN_MIMES,
            backend_kind="local",
            parser_fingerprint=LEGACY_MARKDOWN_FINGERPRINT,
            capabilities=frozenset({
                "headings", "lists", "code_blocks", "blockquotes", "tables",
            }),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{LEGACY_MARKDOWN_PARSER_ID} cannot parse mime {mime!r}"
            )
        text = _decode_utf8(data, LEGACY_MARKDOWN_PARSER_ID)
        try:
            tokens = MarkdownIt().enable("table").parse(text)
            # disable_image_resolution：适配器契约是无 IO 纯函数（§C06），
            # 不解析本地/远程图片——只保留原文 src，图片物化是旧链路
            # image_assets 的职责，不属于影子解析。
            content_blocks = _tokens_to_blocks(
                tokens, context={"disable_image_resolution": True}
            )
        except Exception as exc:  # 第三方异常不得穿越适配层（SRS §C06）
            raise ParserAdapterError(
                f"markdown structure parse failed: {exc}"
            ) from exc

        blocks, warnings = _to_backend_blocks(content_blocks, text)
        return BackendParseArtifact(
            parser_id=LEGACY_MARKDOWN_PARSER_ID,
            parser_version=LEGACY_MARKDOWN_VERSION,
            mime=mime.lower(),
            blocks=tuple(blocks),
            raw_output=text,
            warnings=tuple(warnings),
        )


# ---------------------------------------------------------------------------
# 转换（模块级纯函数，便于单测；token 级块序列本身就是文档阅读顺序）
# ---------------------------------------------------------------------------

def _to_backend_blocks(
    content_blocks: list[ContentBlock], source_text: str
) -> tuple[list[BackendBlock], list[str]]:
    """ContentBlock 序列 -> (BackendBlock 序列, warnings)."""
    lines = source_text.split("\n")
    out: list[BackendBlock] = []
    warnings: list[str] = []
    for block in content_blocks:
        produced, warn = _content_block_to_backend(block, lines)
        out.extend(produced)
        warnings.extend(warn)
    return out, warnings


def _content_block_to_backend(
    block: ContentBlock, lines: list[str]
) -> tuple[list[BackendBlock], list[str]]:
    if block.block_type == "heading":
        return [_heading_block(block)], []
    if block.block_type == "list":
        return _expand_list(block, lines)
    structure: dict[str, Any] = dict(block.structure) if block.structure else {}
    if block.language:
        structure["language"] = block.language
    return [
        BackendBlock(
            block_type=_BLOCK_TYPE_NAMES.get(block.block_type, block.block_type),
            text=block.text,
            line_start=block.line_start,
            line_end=block.line_end,
            structure=structure,
        )
    ], []


def _heading_block(block: ContentBlock) -> BackendBlock:
    """heading 行区间修正为 [line_start, line_start+1)（单行语义）."""
    line_start = block.line_start
    line_end = block.line_end
    if line_start is not None and (line_end is None or line_end <= line_start):
        line_end = line_start + 1
    return BackendBlock(
        block_type="heading",
        text=block.text,
        line_start=line_start,
        line_end=line_end,
        level=block.level if block.level and block.level > 0 else 1,
    )


def _expand_list(
    block: ContentBlock, lines: list[str]
) -> tuple[list[BackendBlock], list[str]]:
    """list block -> (逐条 list_item, warnings).

    回扫到的列表项起始行数与 items_nested 数量不一致时，回退为整体
    一个 list block（不伪造逐项行号，SRS §7.4"缺可以，但应可见"——
    回退时产出 warning 供下游感知逐项行证据缺失）。
    """
    items = list((block.structure or {}).get("items_nested") or [])
    start, end = block.line_start, block.line_end
    if start is None or end is None or not items:
        return [_whole_list_block(block)], [
            f"list block at lines [{start},{end}) lacks item structure; "
            "kept as whole-list block without per-item line evidence"
        ]

    region_end = min(end, len(lines))
    starts = [
        idx for idx in range(start, region_end)
        if _LIST_ITEM_LINE_RE.match(lines[idx])
    ]
    if len(starts) != len(items):
        return [_whole_list_block(block)], [
            f"list block at lines [{start},{region_end}) item/line mismatch "
            f"({len(items)} items vs {len(starts)} marker lines); kept as "
            "whole-list block without per-item line evidence"
        ]

    out: list[BackendBlock] = []
    for k, item in enumerate(items):
        item_start = starts[k]
        item_end = starts[k + 1] if k + 1 < len(starts) else region_end
        while item_end > item_start + 1 and not lines[item_end - 1].strip():
            item_end -= 1  # 去掉区间尾部空行，保证 line_end 精确
        out.append(BackendBlock(
            block_type="list_item",
            text=str(item.get("text", "")),
            line_start=item_start,
            line_end=item_end,
            level=int(item.get("depth", 1) or 1),
        ))
    return out, []


def _whole_list_block(block: ContentBlock) -> BackendBlock:
    structure = dict(block.structure or {})
    structure.setdefault("kind", "list")
    return BackendBlock(
        block_type="list",
        text=block.text,
        line_start=block.line_start,
        line_end=block.line_end,
        structure=structure,
    )


__all__ = [
    "LEGACY_MARKDOWN_FINGERPRINT",
    "LEGACY_MARKDOWN_MIMES",
    "LEGACY_MARKDOWN_PARSER_ID",
    "LEGACY_MARKDOWN_VERSION",
    "LegacyMarkdownParser",
]
