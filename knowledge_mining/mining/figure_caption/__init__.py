"""图片 VLM 图注（方案B：独立算子）。见 prompts/keys/service 各模块 docstring。"""
from knowledge_mining.mining.figure_caption.keys import caption_cache_key
from knowledge_mining.mining.figure_caption.prompts import (
    FIGCAP_PROMPT_VERSION,
    build_user_prompt,
)
from knowledge_mining.mining.figure_caption.pptx_bytes import PptxImageByteSource
from knowledge_mining.mining.figure_caption.service import (
    FIGURE_SEGMENT_INDEX_BASE,
    CaptionOutcome,
    FigureCaptionCache,
    FigureCaptionFacade,
    FigureCaptionParams,
    FigureCaptioner,
    ImageByteSource,
    SegmentAppender,
)

__all__ = [
    "FIGCAP_PROMPT_VERSION",
    "FIGURE_SEGMENT_INDEX_BASE",
    "CaptionOutcome",
    "FigureCaptionCache",
    "FigureCaptionFacade",
    "FigureCaptionParams",
    "FigureCaptioner",
    "ImageByteSource",
    "PptxImageByteSource",
    "SegmentAppender",
    "build_user_prompt",
    "caption_cache_key",
]
