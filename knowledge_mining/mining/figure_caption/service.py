"""图片 VLM 图注门面（方案B 核心服务）。

职责：给定一个快照的 Parse IR，找出"无可索引内容"的 figure 元素
（无文本、无 caption_of 绑定、derived_annotations 为空——方案A/B 互斥），
取图片字节 → 查缓存 → 调 VLM 生成图注 → 追加 figure 切片到快照。

设计（deep module，依赖全部走 Protocol 注入）：
- ``FigureCaptionCache``：图注结果缓存（表 asset_figure_caption_cache，B2 给 PG 实现）；
- ``ImageByteSource``：按 image_sha256 取图片字节（PPTX 实现=从冻结源文件
  重开 python-pptx 建 sha→blob 索引——解析器不持久化图片字节，见
  native_pptx._picture_block）；
- ``FigureCaptioner``：VLM 调用（真实实现包 LlmTaskClient + build_vision_user_message）；
- ``SegmentAppender``：figure 切片幂等追加（B2 给 SegmentStore 加 append 协议）。

失败语义（对齐方案B设计）：单图失败计数继续，永不抛出中断文档；
全部失败时 degraded=True 供算子层决定 FALLBACK 表现。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from knowledge_mining.mining.contracts.parse_ir.types import (
    FigureAsset,
    ParsedDocument,
)
from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentElementLink,
)
from knowledge_mining.mining.figure_caption.keys import caption_cache_key
from knowledge_mining.mining.figure_caption.prompts import (
    FIGCAP_PROMPT_VERSION,
    build_user_prompt,
)

logger = logging.getLogger(__name__)

# figure 追加切片的保留段起始（主链编译 segment_index 从 0 递增，
# 1_000_000 起的保留段保证不与任何真实切片撞号；segment_key 由
# appender 侧定为 f"{document_key}#f{order}"，与主链 #{index} 命名空间隔离）。
FIGURE_SEGMENT_INDEX_BASE = 1_000_000

DEFAULT_MODEL = "glm-4.5v"
DEFAULT_MAX_FIGURES = 20
DEFAULT_MIN_IMAGE_BYTES = 1024


@dataclass(frozen=True)
class FigureCaptionParams:
    """算子参数（catalog options 的服务侧形态）。"""

    model: str = DEFAULT_MODEL
    max_figures: int = DEFAULT_MAX_FIGURES
    min_image_bytes: int = DEFAULT_MIN_IMAGE_BYTES


@dataclass(frozen=True)
class CaptionOutcome:
    """一次快照图注生成的结果计量（进算子 metrics / 审计）。"""

    figures_found: int = 0
    captioned: int = 0
    cache_hits: int = 0
    failed: int = 0
    skipped_small: int = 0
    skipped_missing_bytes: int = 0
    appended: int = 0
    degraded: bool = False

    def as_metrics(self) -> dict[str, Any]:
        return {
            "figures_found": self.figures_found,
            "captioned": self.captioned,
            "cache_hits": self.cache_hits,
            "failed": self.failed,
            "skipped_small": self.skipped_small,
            "skipped_missing_bytes": self.skipped_missing_bytes,
            "appended": self.appended,
            "degraded": self.degraded,
        }


class FigureCaptionCache(Protocol):
    """图注缓存（只缓存成功结果；失败不落缓存，重试有机会）。"""

    async def get(self, key: str) -> str | None: ...

    async def put(self, key: str, caption: str, *, model: str) -> None: ...


class ImageByteSource(Protocol):
    """按 image_sha256 提供图片字节；不可得返回 None（计 skipped）。"""

    async def get_image_bytes(self, image_sha256: str) -> bytes | None: ...


class FigureCaptioner(Protocol):
    """VLM 图注调用（同步阻塞——门面用线程池调度，避免卡事件循环）。"""

    def caption(
        self,
        *,
        image_bytes: bytes,
        mime: str | None,
        native_caption: str | None,
    ) -> str | None: ...


class SegmentAppender(Protocol):
    """figure 切片幂等追加（返回实际新插入条数；重放=零新增）。"""

    async def append_figures_for_snapshot(
        self,
        snapshot_id: str,
        document_key: str,
        segments: tuple[CompiledSegment, ...],
    ) -> int: ...


@dataclass(frozen=True)
class _FigureCandidate:
    element_id: str
    image_sha256: str
    mime: str | None
    native_caption: str
    heading_chain: tuple[tuple[int, str], ...]
    evidence_span_ids: tuple[str, ...]


def _collect_candidates(
    doc: ParsedDocument, *, max_figures: int
) -> list[_FigureCandidate]:
    """挑出需要 VLM 图注的 figure 元素（阅读序，附标题链）。

    排除三类（编译器已覆盖或方案A已处理）：
    ① element.text 非空；② 已有 caption_of 绑定（_emit_figure 已产片）；
    ③ FigureAsset.derived_annotations 非空（方案A 已写图注）。
    """
    caption_bound = {
        rel.target_element_id
        for rel in doc.relations
        if rel.relation_type == "caption_of"
    }
    caption_text = {
        rel.target_element_id: rel.source_element_id for rel in doc.relations
        if rel.relation_type == "caption_of"
    }
    texts = {e.element_id: (e.text or "").strip() for e in doc.elements}
    figures: dict[str, FigureAsset] = {
        asset_id: asset
        for asset_id, asset in doc.structured_assets.items()
        if isinstance(asset, FigureAsset)
    }

    candidates: list[_FigureCandidate] = []
    stack: list[tuple[int, str]] = []
    for element in doc.elements:
        etype = element.element_type
        if etype == "heading":
            level_raw = element.style.get("level")
            level = (
                int(level_raw)
                if isinstance(level_raw, int) and level_raw > 0
                else len(stack) + 1
            )
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, (element.text or "").strip()))
            continue
        if etype != "figure":
            continue
        if texts.get(element.element_id):
            continue  # ①
        if element.element_id in caption_bound:
            continue  # ② 编译器已产片
        asset = figures.get(f"{element.element_id}-figure")
        if asset is None:
            continue
        if getattr(asset, "derived_annotations", None):
            continue  # ③ 方案A 互斥
        sha = getattr(asset, "image_hash", None)
        if not sha:
            continue
        binary = doc.binary_assets.get(
            getattr(asset, "original_image_ref", "") or "", {}
        )
        mime = binary.get("mime") if isinstance(binary, dict) else None
        # 原生图注文本（游离 caption 未绑定但有文本时不阻断：仅作参考传给 VLM）
        native = ""
        caption_el = caption_text.get(element.element_id)
        if caption_el:
            native = texts.get(caption_el, "")
        candidates.append(_FigureCandidate(
            element_id=element.element_id,
            image_sha256=sha,
            mime=mime,
            native_caption=native,
            heading_chain=tuple(stack),
            evidence_span_ids=tuple(
                s.span_id for s in element.source_spans if s.span_id
            ),
        ))
        if len(candidates) >= max_figures:
            break
    return candidates


def _build_segment(
    candidate: _FigureCandidate,
    *,
    caption: str,
    model: str,
    order: int,
) -> CompiledSegment:
    raw = f"{candidate.native_caption} {caption}".strip()
    return CompiledSegment(
        segment_index=FIGURE_SEGMENT_INDEX_BASE + order,
        block_type="figure",
        raw_text=raw,
        heading_chain=candidate.heading_chain,
        element_ids=(candidate.element_id,),
        links=(
            SegmentElementLink(
                element_id=candidate.element_id,
                evidence_span_ids=candidate.evidence_span_ids,
            ),
        ),
        metadata={
            "figure_caption": caption,
            "caption_source": "vlm",
            "vlm_model": model,
            "figure_element_id": candidate.element_id,
            "image_sha256": candidate.image_sha256,
            "native_caption": candidate.native_caption,
            "prompt_version": FIGCAP_PROMPT_VERSION,
        },
    )


class FigureCaptionFacade:
    """门面：IR →（缓存/VLM 图注）→ 追加 figure 切片。"""

    def __init__(
        self,
        *,
        cache: FigureCaptionCache,
        image_byte_source: ImageByteSource,
        captioner: FigureCaptioner,
        appender: SegmentAppender,
    ) -> None:
        self._cache = cache
        self._bytes = image_byte_source
        self._captioner = captioner
        self._appender = appender

    async def generate_for_snapshot(
        self,
        *,
        snapshot_id: str,
        document_key: str,
        domain: str,
        doc: ParsedDocument,
        params: FigureCaptionParams | None = None,
    ) -> CaptionOutcome:
        params = params or FigureCaptionParams()
        candidates = _collect_candidates(doc, max_figures=params.max_figures)
        outcome = CaptionOutcome(figures_found=len(candidates))
        if not candidates:
            return outcome

        source_raw_hash = doc.source_identity.source_raw_hash
        parser_fingerprint = doc.source_identity.parser_fingerprint

        segments: list[CompiledSegment] = []
        captioned = cache_hits = failed = small = missing = 0
        for order, candidate in enumerate(candidates):
            key = caption_cache_key(
                domain=domain,
                source_raw_hash=source_raw_hash,
                parser_fingerprint=parser_fingerprint,
                figure_element_id=candidate.element_id,
                model=params.model,
                prompt_version=FIGCAP_PROMPT_VERSION,
            )
            caption = await self._cache.get(key)
            if caption:
                cache_hits += 1
            else:
                image_bytes = await self._bytes.get_image_bytes(
                    candidate.image_sha256
                )
                if image_bytes is None:
                    missing += 1
                    logger.info(
                        "figure bytes unavailable (sha=%s...)",
                        candidate.image_sha256[:12],
                    )
                    continue
                if len(image_bytes) < params.min_image_bytes:
                    small += 1
                    continue
                try:
                    caption = await asyncio.to_thread(
                        self._captioner.caption,
                        image_bytes=image_bytes,
                        mime=candidate.mime,
                        native_caption=candidate.native_caption or None,
                    )
                except Exception as exc:  # noqa: BLE001 —— 单图失败不中断
                    failed += 1
                    logger.warning(
                        "VLM caption failed for %s: %s",
                        candidate.element_id,
                        exc,
                    )
                    continue
                if not caption or not caption.strip():
                    failed += 1
                    continue
                caption = caption.strip()
                await self._cache.put(key, caption, model=params.model)
                captioned += 1
            segments.append(_build_segment(
                candidate, caption=caption, model=params.model, order=order,
            ))

        appended = 0
        if segments:
            appended = await self._appender.append_figures_for_snapshot(
                snapshot_id, document_key, tuple(segments)
            )
        outcome = CaptionOutcome(
            figures_found=len(candidates),
            captioned=captioned,
            cache_hits=cache_hits,
            failed=failed,
            skipped_small=small,
            skipped_missing_bytes=missing,
            appended=appended,
            degraded=bool(candidates) and (captioned + cache_hits) == 0,
        )
        return outcome


__all__ = [
    "DEFAULT_MAX_FIGURES",
    "DEFAULT_MIN_IMAGE_BYTES",
    "DEFAULT_MODEL",
    "FIGURE_SEGMENT_INDEX_BASE",
    "CaptionOutcome",
    "FigureCaptionCache",
    "FigureCaptionParams",
    "FigureCaptionFacade",
    "FigureCaptioner",
    "ImageByteSource",
    "SegmentAppender",
    "_collect_candidates",
]
