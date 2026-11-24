"""figure_caption_generate 的同步工作流门面（handler 只认本接口）。

职责（对齐 SegmentCompileFacade/QueryExpansionFacade 风格）：
- 按快照加载 Parse IR（共享 ir_access 通道，缺 IR → 空产出降级）；
- 从冻结源文件读回字节建 PPTX sha→blob 索引（仅 pptx；其它格式
  全 miss → skipped_missing_bytes，不阻断）；
- 驱动 FigureCaptionFacade（缓存/VLM/追加）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from knowledge_mining.mining.contracts.storage.types import ObjectLocation
from knowledge_mining.mining.figure_caption.service import (
    CaptionOutcome,
    FigureCaptionFacade,
    FigureCaptionParams,
)

logger = logging.getLogger(__name__)


def _run_sync(coro) -> Any:  # noqa: ANN001
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class _NoImageBytes:
    """非 PPTX 源（PDF/DOCX 字节抽取是 P0，未上线）：全 miss."""

    async def get_image_bytes(self, image_sha256: str) -> bytes | None:
        return None


class FigureCaptionWorkflowFacade:
    """sync 门面：bundle 引用 → 图注产出（CaptionOutcome）。"""

    def __init__(
        self,
        *,
        snapshots: Any,
        storage_objects: Any,
        object_store: Any,
        cache: Any,
        segment_store: Any,
        captioner: Any,
    ) -> None:
        self._snapshots = snapshots
        self._storage_objects = storage_objects
        self._object_store = object_store
        self._cache = cache
        self._segment_store = segment_store
        self._captioner = captioner

    def generate_for_snapshot(
        self,
        *,
        snapshot_id: str | None,
        document_key: str,
        domain: str,
        params: dict,
        frozen_input: Any = None,
    ) -> CaptionOutcome:
        if not snapshot_id:
            return CaptionOutcome()
        params_model = FigureCaptionParams(
            model=str(params.get("model") or "glm-4.5v"),
            max_figures=int(params.get("maxFigures", 20)),
            min_image_bytes=int(params.get("minImageBytes", 1024)),
        )
        return _run_sync(self._generate_async(
            snapshot_id=snapshot_id,
            document_key=document_key,
            domain=domain,
            params=params_model,
            frozen_input=frozen_input,
        ))

    async def _generate_async(
        self,
        *,
        snapshot_id: str,
        document_key: str,
        domain: str,
        params: FigureCaptionParams,
        frozen_input: Any,
    ) -> CaptionOutcome:
        from knowledge_mining.mining.snapshot_store.ir_access import (
            SnapshotIRUnavailable,
            load_parsed_document,
        )

        try:
            doc = await load_parsed_document(
                snapshots=self._snapshots,
                storage_objects=self._storage_objects,
                object_store=self._object_store,
                snapshot_id=snapshot_id,
            )
        except SnapshotIRUnavailable as exc:
            logger.info(
                "figure caption skipped (no IR) for snapshot %s: %s",
                snapshot_id, exc,
            )
            return CaptionOutcome()

        byte_source = await self._build_byte_source(frozen_input)
        facade = FigureCaptionFacade(
            cache=self._cache,
            image_byte_source=byte_source,
            captioner=self._captioner,
            appender=self._segment_store,
        )
        return await facade.generate_for_snapshot(
            snapshot_id=snapshot_id,
            document_key=document_key,
            domain=domain,
            doc=doc,
            params=params,
        )

    async def _build_byte_source(self, frozen_input: Any) -> Any:
        mime = str(getattr(frozen_input, "mime", "") or "")
        if "presentationml" not in mime and not mime.endswith(".pptx"):
            return _NoImageBytes()
        bucket = str(getattr(frozen_input, "bucket", "") or "")
        object_key = str(getattr(frozen_input, "object_key", "") or "")
        if not bucket or not object_key:
            return _NoImageBytes()
        location = ObjectLocation(
            bucket=bucket,
            object_key=object_key,
            version_id=getattr(frozen_input, "object_version_id", None),
        )
        chunks: list[bytes] = []
        try:
            async for chunk in self._object_store.get_stream(location):
                chunks.append(chunk)
        except Exception as exc:  # noqa: BLE001 —— 源对象不可读：全 miss 降级
            logger.warning(
                "figure caption source unreadable (%s/%s): %s",
                bucket, object_key, exc,
            )
            return _NoImageBytes()
        from knowledge_mining.mining.figure_caption.pptx_bytes import (
            PptxImageByteSource,
        )

        return PptxImageByteSource(b"".join(chunks))


__all__ = ["FigureCaptionWorkflowFacade"]
