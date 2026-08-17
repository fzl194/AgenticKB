"""PPTX 原生解析适配器 + Normalizer（M3, SRS §C06/§C07, ADR-0003 D-028A）.

理念：全部用工业级成熟库（python-pptx），自研代码只做"库输出 ->
BackendBlock"映射。

- ``NativePptxParser``：每 slide 一个 ``container_ref``（index）；shape 顺序
  遍历：
  - title placeholder（``PP_PLACEHOLDER.TITLE`` / ``CENTER_TITLE``）->
    heading level 1；
  - 有文字的 text_frame -> paragraph；
  - picture shape -> figure 块；
  - table shape -> 表格块（``is_merge_origin`` / ``span_width`` /
    ``span_height`` 库 API 直读合并，``is_spanned`` 覆盖格不产 cell）。
  - bbox 用 shape.left/top/width/height（EMU 原样保留，不换算不伪造），
    native_ref={"slide_index": i, "shape_index": j}。
- ``PptxNormalizer``：slide 容器（无页码伪造，order_index 即幻灯顺序，
  coordinate_unit="emu"，容器尺寸来自 presentation 的 EMU 物理尺寸）；
  元素 EvidenceSpan(visual_region=bbox+unit, page_id=slide 容器)。

fingerprint：``native_pptx@1.0.0#python-pptx-<ver>``。
"""
from __future__ import annotations

import io
from importlib.metadata import version as _pkg_version
from typing import Any

from pptx import Presentation as OpenPresentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.shapes.base import BaseShape

from knowledge_mining.mining.contracts.parse_ir import (
    Container,
    EvidenceSpan,
)
from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
    ParserAdapterError,
    ParserDescriptor,
    UnsupportedFormat,
)
from knowledge_mining.mining.parse_adapters.native._base import (
    BaseNativeNormalizer,
)

NATIVE_PPTX_PARSER_ID = "native_pptx"
NATIVE_PPTX_VERSION = "1.0.0"
_PYTHON_PPTX_VERSION = _pkg_version("python-pptx")
NATIVE_PPTX_FINGERPRINT = (
    f"{NATIVE_PPTX_PARSER_ID}@{NATIVE_PPTX_VERSION}"
    f"#python-pptx-{_PYTHON_PPTX_VERSION}"
)

NATIVE_PPTX_MIMES = frozenset({
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
})

_TITLE_PLACEHOLDER_TYPES = frozenset({
    PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE,
})


class NativePptxParser:
    """DocumentParser 实现：python-pptx 包装（SRS §C06）."""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=NATIVE_PPTX_PARSER_ID,
            display_name="Native PPTX Parser (python-pptx)",
            version=NATIVE_PPTX_VERSION,
            supported_mimes=NATIVE_PPTX_MIMES,
            backend_kind="local",
            parser_fingerprint=NATIVE_PPTX_FINGERPRINT,
            capabilities=frozenset({"slides", "titles", "shapes", "tables"}),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{NATIVE_PPTX_PARSER_ID} cannot parse mime {mime!r}"
            )
        try:
            prs = OpenPresentation(io.BytesIO(data))
        except Exception as exc:  # 第三方异常不得穿越适配层（SRS §C06）
            raise ParserAdapterError(
                f"{NATIVE_PPTX_PARSER_ID}: python-pptx failed to open source: {exc}"
            ) from exc

        blocks: list[BackendBlock] = []
        try:
            for slide_index, slide in enumerate(prs.slides):
                for shape_index, shape in enumerate(slide.shapes):
                    block = _shape_block(shape, slide_index, shape_index)
                    if block is not None:
                        blocks.append(block)
        except Exception as exc:  # 中段损坏也归一（§C06，评审 MED）
            raise ParserAdapterError(
                f"{NATIVE_PPTX_PARSER_ID}: failed to walk slides: {exc}"
            ) from exc

        usage: dict[str, Any] = {
            "slide_width_emu": int(prs.slide_width),
            "slide_height_emu": int(prs.slide_height),
        }
        return BackendParseArtifact(
            parser_id=NATIVE_PPTX_PARSER_ID,
            parser_version=NATIVE_PPTX_VERSION,
            mime=mime.lower(),
            blocks=tuple(blocks),
            usage=usage,
        )


# ---------------------------------------------------------------------------
# Parser 映射（模块级纯函数，便于单测）
# ---------------------------------------------------------------------------

def _shape_block(
    shape: BaseShape, slide_index: int, shape_index: int
) -> BackendBlock | None:
    """一个 shape -> 块；无内容（空文本框等）返回 None."""
    common = {
        "container_ref": {"container_type": "slide", "index": slide_index},
        "native_ref": {"slide_index": slide_index, "shape_index": shape_index},
        "bbox": _bbox_of(shape),
    }
    if shape.has_table:
        return _table_block(shape, common)
    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        return BackendBlock(
            block_type="figure",
            text="",
            structure={"name": shape.name},
            **common,
        )
    if shape.has_text_frame:
        text = shape.text_frame.text.strip()
        if not text:
            return None
        if _is_title_placeholder(shape):
            return BackendBlock(block_type="heading", text=text, level=1, **common)
        return BackendBlock(block_type="paragraph", text=text, **common)
    return None


def _table_block(shape: BaseShape, common: dict[str, Any]) -> BackendBlock:
    """pptx table -> table 块（is_merge_origin/is_spanned 库 API 直读）."""
    table = shape.table
    rows = len(table.rows)
    cols = len(table.columns)
    cells: list[dict[str, Any]] = []
    for r, row in enumerate(table.rows):
        for c, cell in enumerate(row.cells):
            if cell.is_spanned:
                continue  # 被合并覆盖的格不产 cell
            if cell.is_merge_origin:
                row_span, col_span = int(cell.span_height), int(cell.span_width)
            else:
                row_span, col_span = 1, 1
            cells.append({
                "row_index": r,
                "column_index": c,
                "text": cell.text.strip(),
                "row_span": row_span,
                "column_span": col_span,
                "is_header": r == 0,  # 首行表头约定
            })
    structure: dict[str, Any] = {"rows": rows, "cols": cols, "cells": cells}
    return BackendBlock(
        block_type="table",
        text="",
        structure=structure,
        **common,
    )


def _bbox_of(shape: BaseShape) -> tuple[float, float, float, float] | None:
    """shape 位置（EMU 原样保留）；无位置的 shape 返回 None 不伪造."""
    if None in (shape.left, shape.top, shape.width, shape.height):
        return None
    return (
        float(shape.left), float(shape.top),
        float(shape.width), float(shape.height),
    )


def _is_title_placeholder(shape: BaseShape) -> bool:
    if not shape.is_placeholder:
        return False
    return shape.placeholder_format.type in _TITLE_PLACEHOLDER_TYPES


# ---------------------------------------------------------------------------
# Normalizer（SRS §C07）
# ---------------------------------------------------------------------------

class PptxNormalizer(BaseNativeNormalizer):
    """PPTX backend artifact -> Parse IR：slide 容器 + EMU 坐标证据."""

    normalizer_version = "native-pptx@1"
    _default_fingerprints = {NATIVE_PPTX_PARSER_ID: NATIVE_PPTX_FINGERPRINT}
    # slide 是独立内容单元：父链不跨 slide（避免无标题 slide 的正文
    # 误挂上一张 slide 的标题，评审 MED）。
    reset_heading_stack_on_container_change = True
    _element_type_map = {
        "heading": "heading",
        "paragraph": "paragraph",
        "table": "table",
        "figure": "figure",
    }

    def _build_containers(self, artifact) -> tuple[Container, ...]:
        slide_indices = sorted({
            (b.container_ref or {}).get("index")
            for b in artifact.blocks if b.container_ref
        }, key=lambda v: (v is None, v))
        slide_ids = {index: f"c-slide-{index}" for index in slide_indices}
        width = artifact.usage.get("slide_width_emu")
        height = artifact.usage.get("slide_height_emu")
        return tuple(
            Container(
                container_id=slide_ids[index],
                container_type="slide",
                order_index=int(index),
                name=None,  # 不伪造页码/名称（SRS §3.6）
                width=float(width) if width is not None else None,
                height=float(height) if height is not None else None,
                coordinate_unit="emu",
            )
            for index in slide_indices
        )

    def _container_id_for(self, block, containers) -> str | None:
        index = (block.container_ref or {}).get("index")
        for container in containers:
            if container.container_type == "slide":
                if container.order_index == index:
                    return container.container_id
        return None

    def _make_spans(self, element_id, block, container_id) -> tuple[EvidenceSpan, ...]:
        visual = None
        if block.bbox is not None:
            visual = {
                "bbox": [block.bbox[0], block.bbox[1], block.bbox[2], block.bbox[3]],
                "unit": "emu",
            }
        return (EvidenceSpan(
            span_id=f"{element_id}-s0",
            page_id=container_id,
            visual_region=visual,
            native_ref=dict(block.native_ref) if block.native_ref else None,
            raw_text=block.text or None,
        ),)


__all__ = [
    "NATIVE_PPTX_FINGERPRINT",
    "NATIVE_PPTX_MIMES",
    "NATIVE_PPTX_PARSER_ID",
    "NATIVE_PPTX_VERSION",
    "NativePptxParser",
    "PptxNormalizer",
]
