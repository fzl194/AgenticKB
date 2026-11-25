"""PPTX 图片字节源：从冻结源文件重开 python-pptx 建 sha→blob 索引。

背景：native_pptx._picture_block 只把 sha256/size/mime 写进 IR
（binary_assets 是元数据，不落字节）——图注算子取图必须回源。
实现与解析器同路（python-pptx shape.image.blob），sha 口径一致。
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


class PptxImageByteSource:
    """按需建索引（首个请求触发一次全文扫描，之后常驻内存）。"""

    def __init__(self, source_bytes: bytes) -> None:
        self._source_bytes = source_bytes
        self._index: dict[str, bytes] | None = None

    async def get_image_bytes(self, image_sha256: str) -> bytes | None:
        if self._index is None:
            self._index = _build_sha_index(self._source_bytes)
        return self._index.get(image_sha256)


def _build_sha_index(source_bytes: bytes) -> dict[str, bytes]:
    import hashlib

    from pptx import Presentation  # noqa: PLC0415 —— 惰性导入，与解析器同源

    index: dict[str, bytes] = {}
    try:
        presentation = Presentation(io.BytesIO(source_bytes))
    except Exception as exc:  # noqa: BLE001 —— 损坏源文件：全 miss 不中断
        logger.warning("pptx reopen failed: %s", exc)
        return index
    for slide in presentation.slides:
        for shape in slide.shapes:
            try:
                blob = shape.image.blob
            except Exception:  # noqa: BLE001 —— 非图片 shape / 读取失败
                continue
            if not blob:
                continue
            index[hashlib.sha256(blob).hexdigest()] = blob
    return index


__all__ = ["PptxImageByteSource"]
