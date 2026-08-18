"""Native PDF Normalizer（M3, SRS §C07 / §4.7, ADR-0003 D-028A）.

要点：
- 每页一个 ``Container(container_type="page")``，``page_number`` 1 基，
  ``order_index`` 即页序；宽高取自 artifact.usage（缺则 None 不伪造）。
- Element：heading/paragraph/table（未映射词表落 ``unknown`` 并记
  warning）；EvidenceSpan 带 ``visual_region={"bbox": [...],
  "page_index": N}`` + ``raw_text`` + ``text_range=(0, len)``，行号类
  source_locator 不适用（PDF 无行语义，不伪造）。
- 关系：页内 ``next_in_reading_order``（跨页不连）；heading 弹栈父链
  （复用 M2 思路的最小复制实现，不改 M2 文件）产出 parent_id +
  ``parent_of``。
- TableAsset：structure 的 rows/cols/cells（含 row_span/col_span）映射为
  TableCell；首行 ``is_header=True`` 是约定而非事实，``confidence.type``
  降权且 provenance 注明；跨页 ``continuation_of`` 留空（M4 Reconciler）。
- ``warning`` 块（如无文本层页）不生成元素，转入 diagnostics.warnings，
  不伪造内容（SRS §7.4）。
- ``stable_element_id(scope=source_raw_hash, order_index)``；产出必过
  ``parse_ir.validate``，error 即 raise ValueError（SRS §4.7）。
"""
from __future__ import annotations

from typing import Any

from knowledge_mining.mining.contracts.parse_ir import (
    PARSE_IR_SCHEMA_VERSION,
    Confidence,
    Container,
    Diagnostics,
    Element,
    EvidenceSpan,
    ParseIdentity,
    ParsedDocument,
    Relation,
    TableAsset,
    TableCell,
    stable_element_id,
    validate,
)
from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
)
from knowledge_mining.mining.parse_adapters.native_pdf import (
    NATIVE_PDF_FINGERPRINT,
    NATIVE_PDF_PARSER_ID,
)
from knowledge_mining.mining.parse_adapters.rendered_text import render_table_text

PDF_NORMALIZER_VERSION = "pdf-native@1"

# 首行当表头是映射约定，非检测事实 -> type 置信度降权。
TABLE_HEADER_CONVENTION_CONFIDENCE = 0.6

# backend block_type -> Parse IR element_type（SRS §4.7 映射表）。
_BLOCK_TYPE_TO_ELEMENT_TYPE: dict[str, str] = {
    "heading": "heading",
    "paragraph": "paragraph",
    "table": "table",
    # 家具标注（适配层 classify_furniture 产出，SRS §7.3 合法元素类型）：
    # 保留内容但可被下游按类型过滤/去重（M4 Reconciler 消费）。
    "page_header": "page_header",
    "page_footer": "page_footer",
    "page_number": "page_number",
}


class PdfNormalizer:
    """ParseIRNormalizer 实现：页导向 backend artifact -> Parse IR（§C07）."""

    def normalize(
        self,
        artifact: BackendParseArtifact,
        *,
        source_raw_hash: str,
        parse_run_id: str | None = None,
    ) -> ParsedDocument:
        warnings = list(artifact.warnings)
        containers = _build_containers(artifact.blocks, artifact.usage)
        elements, relations, assets = _build_element_graph(
            artifact.blocks, source_raw_hash, warnings
        )
        doc = ParsedDocument(
            schema_version=PARSE_IR_SCHEMA_VERSION,
            source_identity=ParseIdentity(
                source_raw_hash=source_raw_hash,
                parser_fingerprint=(
                    NATIVE_PDF_FINGERPRINT
                    if artifact.parser_id == NATIVE_PDF_PARSER_ID
                    else f"{artifact.parser_id}@{artifact.parser_version}"
                ),
                normalizer_version=PDF_NORMALIZER_VERSION,
            ),
            containers=tuple(containers),
            elements=tuple(elements),
            relations=tuple(relations),
            structured_assets=assets,
            diagnostics=Diagnostics(
                parser_name=artifact.parser_id,
                parser_version=artifact.parser_version,
                warnings=tuple(warnings),
                errors=tuple(artifact.errors),
                backend_provenance={
                    "parser_id": artifact.parser_id,
                    "mime": artifact.mime,
                },
            ),
            parse_run_id=parse_run_id,
        )
        result = validate(doc)
        if not result.valid:
            errors = "; ".join(
                f"[{issue.code}] {issue.message}"
                for issue in result.issues if issue.level == "error"
            )
            raise ValueError(f"normalization failed validation: {errors}")
        return doc


# ---------------------------------------------------------------------------
# 模块级纯函数（便于单测）
# ---------------------------------------------------------------------------


def _container_id(page_index: int) -> str:
    return f"c-page-{page_index:04d}"


def _build_containers(
    blocks: tuple[BackendBlock, ...], usage: dict[str, Any]
) -> list[Container]:
    """按 container_ref 出现的 page index 造页容器（usage 提供宽高）."""
    indexes = sorted({
        int(b.container_ref["index"])
        for b in blocks
        if b.container_ref and b.container_ref.get("container_type") == "page"
    })
    sizes = usage.get("page_sizes") or []
    return [
        Container(
            container_id=_container_id(idx),
            container_type="page",
            order_index=idx,
            name=f"page {idx + 1}",
            page_number=idx + 1,  # 1 基（SRS §3.6）
            width=float(sizes[idx][0]) if idx < len(sizes) else None,
            height=float(sizes[idx][1]) if idx < len(sizes) else None,
            coordinate_unit="pt",
        )
        for idx in indexes
    ]


def _build_element_graph(
    blocks: tuple[BackendBlock, ...],
    source_raw_hash: str,
    warnings: list[str],
) -> tuple[list[Element], list[Relation], dict[str, TableAsset]]:
    """块序列 -> 元素图：类型映射、页证据 span、heading 父链、表格资产.

    ``warning`` 块不生成元素（无文本层页不伪造内容），转为
    diagnostics warning。
    """
    elements: list[Element] = []
    relations: list[Relation] = []
    assets: dict[str, TableAsset] = {}
    heading_stack: list[tuple[int, str]] = []
    prev_by_page: dict[int, str] = {}

    for order, block in enumerate(blocks):
        if block.block_type == "warning":
            warnings.append(
                f"warning block on page "
                f"{_page_index(block)}: {block.structure.get('reason', '?')}"
            )
            continue
        element_type = _BLOCK_TYPE_TO_ELEMENT_TYPE.get(block.block_type)
        if element_type is None:
            element_type = "unknown"
            warnings.append(
                f"unmapped block_type {block.block_type!r} -> 'unknown'"
            )
        element_id = stable_element_id(source_raw_hash, len(elements))
        parent_id = _resolve_parent(
            heading_stack, block, element_type, element_id
        )
        page_index = _page_index(block)

        cell_spans = _make_cell_spans(element_id, block, page_index)
        asset: TableAsset | None = None
        if element_type == "table":
            asset = _pdf_table_asset(element_id, block, page_index, cell_spans)
            if asset is not None:
                assets[asset.table_id] = asset
        # 整改轮 I-2/I-3：表格 Element.text 是 TableAsset 的统一 rendered
        # view；PDF 无行号语义，不伪造 source_locator。
        text = render_table_text(asset) if asset is not None else block.text

        elements.append(Element(
            element_id=element_id,
            element_type=element_type,
            order_index=len(elements),
            text=text,
            normalized_text=text.strip(),
            parent_id=parent_id,
            page_span_ids=(_container_id(page_index),),
            source_spans=(
                _make_span(element_id, block, page_index), *cell_spans,
            ),
            style=_make_style(block),
            confidence=_make_confidence(block),
            parser_annotations=_make_annotations(block),
        ))
        relations.extend(_element_relations(
            prev_by_page.get(page_index), parent_id, element_id
        ))
        prev_by_page[page_index] = element_id

    return elements, relations, assets


def _page_index(block: BackendBlock) -> int:
    """块的 0 基页码；缺 container_ref 时 -1（不伪造页证据）."""
    if block.container_ref and "index" in block.container_ref:
        return int(block.container_ref["index"])
    return -1


def _resolve_parent(
    heading_stack: list[tuple[int, str]],
    block: BackendBlock,
    element_type: str,
    element_id: str,
) -> str | None:
    """heading 弹栈建链；非 heading 挂到最近标题下（无则 None）."""
    if element_type == "heading":
        level = block.level if block.level and block.level > 0 else 1
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        parent = heading_stack[-1][1] if heading_stack else None
        heading_stack.append((level, element_id))
        return parent
    return heading_stack[-1][1] if heading_stack else None


def _make_span(
    element_id: str, block: BackendBlock, page_index: int
) -> EvidenceSpan:
    """页内 bbox 证据 span（PDF 无行号语义，不伪造 source_locator）."""
    visual_region: dict[str, Any] | None = None
    if block.bbox is not None:
        visual_region = {
            "bbox": [float(v) for v in block.bbox],
            "page_index": page_index,
        }
    return EvidenceSpan(
        span_id=f"{element_id}-s0",
        page_id=_container_id(page_index) if page_index >= 0 else None,
        text_range=(0, len(block.text)),
        visual_region=visual_region,
        raw_text=block.text or None,
    )


def _make_cell_spans(
    element_id: str, block: BackendBlock, page_index: int
) -> tuple[EvidenceSpan, ...]:
    """表格 cell 级 EvidenceSpan（每 cell 自身 bbox，整改轮 I-4）.

    pdfplumber 的 rows[i].cells[j] 提供每格坐标——与 structure["cells"]
    的 ``bbox``/``evidence_index`` 对齐。
    """
    if block.block_type != "table":
        return ()
    structure = block.structure or {}
    raw_cells = structure.get("cells") or []
    spans: list[EvidenceSpan] = []
    for k, cell in enumerate(raw_cells):
        bbox = cell.get("bbox")
        visual = None
        if bbox:
            visual = {
                "bbox": [float(v) for v in bbox],
                "page_index": page_index,
            }
        spans.append(EvidenceSpan(
            span_id=f"{element_id}-cell-{k:04d}",
            page_id=_container_id(page_index) if page_index >= 0 else None,
            visual_region=visual,
            native_ref={"page": page_index, "cell": [
                int(cell["row_index"]), int(cell["column_index"]),
            ]},
            raw_text=str(cell.get("text", "")) or None,
        ))
    return tuple(spans)


def _make_style(block: BackendBlock) -> dict[str, object]:
    style: dict[str, object] = {}
    if block.level is not None:
        style["level"] = block.level
    return style


def _make_confidence(block: BackendBlock) -> Confidence:
    """字号启发式 heading / 约定表头 -> type 降权，不冒充高置信."""
    declared = (block.structure or {}).get("type_confidence")
    if declared is None:
        return Confidence(source=NATIVE_PDF_PARSER_ID)
    return Confidence(
        type=float(declared),
        source=NATIVE_PDF_PARSER_ID,
    )


def _make_annotations(block: BackendBlock) -> dict[str, Any]:
    """结构启发式标注透传（heading_rule / 行字号 / 众数字号）."""
    structure = block.structure or {}
    keys = ("heading_rule", "line_size", "modal_size")
    picked = {k: structure[k] for k in keys if k in structure}
    if block.native_ref:
        picked["native_ref"] = dict(block.native_ref)
    return picked


def _element_relations(
    prev_id: str | None, parent_id: str | None, element_id: str
) -> list[Relation]:
    """parent_of + 页内 next_in_reading_order（跨页不连）."""
    out: list[Relation] = []
    if parent_id is not None:
        out.append(Relation(
            source_element_id=parent_id,
            target_element_id=element_id,
            relation_type="parent_of",
            method=PDF_NORMALIZER_VERSION,
        ))
    if prev_id is not None:
        out.append(Relation(
            source_element_id=prev_id,
            target_element_id=element_id,
            relation_type="next_in_reading_order",
            method=PDF_NORMALIZER_VERSION,
        ))
    return out


def _pdf_table_asset(
    element_id: str,
    block: BackendBlock,
    page_index: int,
    cell_spans: tuple[EvidenceSpan, ...] = (),
) -> TableAsset | None:
    """table 块 structure（rows/cols/cells 含 span）-> TableAsset.

    首行 ``is_header=True`` 为映射约定：confidence.type 降权并在
    provenance 注明；``continuation_of`` 由 Reconciler 回填（不伪造）。
    cell 级 ``source_span_id`` 经 ``evidence_index`` 关联（整改轮 I-4）。
    """
    structure = block.structure or {}
    raw_cells = structure.get("cells") or []
    rows = int(structure.get("rows") or 0)
    cols = int(structure.get("cols") or 0)
    if not raw_cells or rows <= 0 or cols <= 0:
        return None

    def _cell_span_id(c: dict[str, Any]) -> str | None:
        idx = c.get("evidence_index")
        if isinstance(idx, int) and 0 <= idx < len(cell_spans):
            return cell_spans[idx].span_id
        return None

    cells = tuple(
        TableCell(
            row_index=int(c["row_index"]),
            column_index=int(c["column_index"]),
            text=str(c.get("text", "")),
            row_span=int(c.get("row_span", 1)),
            column_span=int(c.get("column_span", 1)),
            is_header=int(c["row_index"]) == 0,
            source_span_id=_cell_span_id(c),
        )
        for c in raw_cells
    )
    has_header = any(c.row_index == 0 for c in cells)
    return TableAsset(
        table_id=f"{element_id}-table",
        page_span_ids=(_container_id(page_index),) if page_index >= 0 else (),
        rows=rows,
        columns=cols,
        cells=cells,
        header_regions=((0, 0),) if has_header else (),
        continuation_of=None,  # Reconciler 职责，不伪造
        confidence=Confidence(
            type=TABLE_HEADER_CONVENTION_CONFIDENCE,
            source=PDF_NORMALIZER_VERSION,
        ),
    )


__all__ = [
    "PDF_NORMALIZER_VERSION",
    "PdfNormalizer",
]
