"""PptxImageByteSource：sha 索引与解析器同口径（python-pptx shape.image.blob）。"""
import hashlib
import io

import pytest

from knowledge_mining.mining.figure_caption.pptx_bytes import PptxImageByteSource

pytestmark = pytest.mark.asyncio


def _make_pptx_with_image(png_bytes: bytes) -> bytes:
    from pptx import Presentation
    from pptx.util import Emu

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_picture(
        io.BytesIO(png_bytes), Emu(0), Emu(0), Emu(Emu(1)), height=Emu(1)
    )
    buf = io.BytesIO()
    presentation.save(buf)
    return buf.getvalue()


def _png_bytes(size: int = 2048) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(200, 30, 30)).save(buf, format="PNG")
    data = buf.getvalue()
    return data


async def test_index_matches_parser_sha_convention():
    blob = _png_bytes()
    source = _make_pptx_with_image(blob)
    byte_source = PptxImageByteSource(source)
    sha = hashlib.sha256(blob).hexdigest()
    assert await byte_source.get_image_bytes(sha) == blob
    assert await byte_source.get_image_bytes("dead" * 16) is None


async def test_corrupt_source_returns_none():
    byte_source = PptxImageByteSource(b"not a pptx")
    assert await byte_source.get_image_bytes("a" * 64) is None
