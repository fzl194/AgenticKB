"""VLM image captioning for PDF image ContentBlocks.

Invoked from ``segment_stage`` (not a standalone pipeline stage). Walks a
SectionNode tree, finds ``block_type="image"`` blocks with empty captions,
calls llm_service vision chat, and returns a new tree with ``text`` filled
in. Failures fall back to native_caption / placeholder — never abort the
document.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from knowledge_mining.mining.contracts.models import ContentBlock, SectionNode

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "glm-4.5v"
_DEFAULT_PROMPT = (
    "请用一两句中文描述这张图在技术文档中的含义。"
    "关注图中的结构、标注、流程或关键对象；不要编造看不清的细节。"
    "只输出图注正文，不要前缀「图注：」。"
)


class ImageCaptioner:
    """Caption PDF image blocks via llm_service multimodal /execute."""

    def __init__(
        self,
        base_url: str,
        *,
        knowledge_domain: str | None = None,
        model: str = _DEFAULT_MODEL,
        max_images: int = 20,
        prompt: str = _DEFAULT_PROMPT,
        enabled: bool = True,
    ) -> None:
        from knowledge_mining.mining.infra.llm_client import LlmClient

        self._client = LlmClient(base_url=base_url)
        self._knowledge_domain = knowledge_domain
        self.model = model
        self.max_images = max_images
        self.prompt = prompt
        self.enabled = enabled

    def caption_tree(self, tree: SectionNode) -> SectionNode:
        """Return a new tree with image captions filled where possible."""
        if not self.enabled or tree is None:
            return tree
        remaining = [self.max_images]

        def _walk(node: SectionNode) -> SectionNode:
            new_blocks: list[ContentBlock] = []
            for block in node.blocks:
                if (
                    block.block_type == "image"
                    and not (block.text or "").strip()
                    and remaining[0] > 0
                ):
                    remaining[0] -= 1
                    new_blocks.append(self._caption_block(block))
                else:
                    new_blocks.append(block)
            children = tuple(_walk(child) for child in node.children)
            return SectionNode(
                title=node.title,
                level=node.level,
                children=children,
                blocks=tuple(new_blocks),
            )

        return _walk(tree)

    def _caption_block(self, block: ContentBlock) -> ContentBlock:
        structure = dict(block.structure or {})
        native = (structure.get("native_caption") or "").strip()
        image_path = structure.get("image_path")
        fallback = native or _placeholder(structure)

        if not image_path or not Path(str(image_path)).is_file():
            structure["caption_source"] = "missing_file"
            return ContentBlock(
                block_type="image",
                text=fallback,
                language=block.language,
                level=block.level,
                line_start=block.line_start,
                line_end=block.line_end,
                structure=structure,
            )

        try:
            from llm_service.providers.multimodal import build_vision_user_message

            prompt = self.prompt
            if native:
                prompt = (
                    f"{self.prompt}\n\n文档中的原始图注/表注（可参考）：{native}"
                )
            messages = [
                {"role": "system", "content": "你是技术文档图注助手。"},
                build_vision_user_message(prompt, image_path=image_path),
            ]
            resp = self._client.execute(
                messages=messages,
                model=self.model,
                expected_output_type="text",
                pipeline_stage="segment",
                knowledge_domain=self._knowledge_domain,
            )
            caption = _extract_text(resp)
            if caption:
                structure["caption_source"] = "vlm"
                structure["vlm_caption"] = caption
                structure["vlm_model"] = self.model
                return ContentBlock(
                    block_type="image",
                    text=caption,
                    language=block.language,
                    level=block.level,
                    line_start=block.line_start,
                    line_end=block.line_end,
                    structure=structure,
                )
        except Exception as exc:
            logger.warning("VLM caption failed for %s: %s", image_path, exc)

        structure["caption_source"] = "fallback"
        return ContentBlock(
            block_type="image",
            text=fallback,
            language=block.language,
            level=block.level,
            line_start=block.line_start,
            line_end=block.line_end,
            structure=structure,
        )


def _placeholder(structure: dict[str, Any]) -> str:
    page = structure.get("page")
    if page is not None:
        return f"[image p.{page}]"
    return "[image]"


def _extract_text(resp: dict | None) -> str | None:
    if not resp:
        return None
    data = resp.get("data", resp)
    if data.get("status") not in (None, "succeeded"):
        return None
    result = data.get("result") or {}
    text = result.get("text_output")
    if isinstance(text, str) and text.strip():
        return text.strip()
    parsed = result.get("parsed_output")
    if isinstance(parsed, str) and parsed.strip():
        return parsed.strip()
    if isinstance(parsed, dict):
        for key in ("caption", "text", "description"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None
