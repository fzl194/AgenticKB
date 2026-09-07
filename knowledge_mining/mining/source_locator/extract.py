"""A1 来源记录纯投影（37/38 号）：Parse IR 位置实值 → 表示级 LocatorRecord.

设计约束（38 号 §2.2/§2.3）：

- **纯函数、零 IO**——只依赖 contracts（ParsedDocument / RetrievalRepresentation），
  每格式 fixture 在本层单测覆盖；
- **不改编译器/切片/snapshot 指纹**——位置实值从 IR 现算，CompiledSegment 与
  compiler_fingerprint 零改动，既有快照与已发放 ref 不受影响；
- **不伪造**——IR 三通道（source_locator / visual_region+页容器 / native_ref）
  全空时落 ``unavailable``，绝不估页码估行号（PDF 无行语义同理，pdf_normalizer
  契约）。

kind 判定优先级：page > line_range > sheet_cell > native；section/document
级表示恒 ``section_only``（34 号：所有可引用片段至少 L1）；alias 型表示
（query_alias/summary_alias）**不产行**——别名命中回 canonical 证据取位。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from knowledge_mining.mining.contracts.parse_ir.types import (
    Container,
    Element,
    EvidenceSpan,
    ParsedDocument,
)
from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrievalRepresentation,
)

#: 物化器版本（取值规则或字段语义变化必须递增——重放幂等键的组成部分）。
LOCATOR_VERSION = "source-locator@1"

#: 进"来源可解析率"分母的 kind（37 号 NFR 口径；审计视图同口径）。
PRECISE_LOCATOR_KINDS = frozenset({"page", "line_range", "sheet_cell"})

#: 不产行的表示类型：别名命中回 canonical（hydrate 按 canonical 取 locator）。
ALIAS_REPRESENTATION_TYPES = frozenset({"query_alias", "summary_alias"})

_SECTION_ELEMENT_TYPES = frozenset({"heading", "title"})

#: parser_fingerprint 前缀 → 用户口径格式（可解析率分母按格式分列）。
_PARSER_PREFIX_FORMATS: Mapping[str, str] = {
    "native_pdf": "pdf",
    "pdf_text_layer": "pdf",
    "legacy_markdown": "md",
    "legacy_txt": "txt",
    "native_xlsx": "xlsx",
    "native_docx": "docx",
    "native_html": "html",
    "native_pptx": "pptx",
    "rendered_text": "txt",
}


@dataclass(frozen=True)
class LocatorRecord:
    """一条表示级来源记录（DDL 014 asset_source_locators 行）."""

    representation_id: str
    target_ref: str
    document_ref: str
    source_format: str
    locator_kind: str
    section_path: str | None = None
    section_element_id: str | None = None
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    sheet: str | None = None
    cell: str | None = None
    table_ref: str | None = None
    row_index: int | None = None
    native_ref: dict[str, Any] | None = None
    description: str | None = None


def source_format_of(parser_fingerprint: str | None) -> str:
    """parser_fingerprint（``native_pdf@…``）→ 格式口径（pdf/md/…）."""
    prefix = str(parser_fingerprint or "").split("@", 1)[0]
    return _PARSER_PREFIX_FORMATS.get(prefix, "other")


# ---------------------------------------------------------------------------
# IR 索引
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _IrIndex:
    spans: Mapping[str, EvidenceSpan] = field(default_factory=dict)
    elements: Mapping[str, Element] = field(default_factory=dict)
    containers: Mapping[str, Container] = field(default_factory=dict)

    @classmethod
    def of(cls, doc: ParsedDocument) -> "_IrIndex":
        spans: dict[str, EvidenceSpan] = {}
        elements: dict[str, Element] = {}
        for element in doc.elements:
            elements.setdefault(element.element_id, element)
            for span in element.source_spans:
                spans.setdefault(span.span_id, span)
        containers = {
            container.container_id: container for container in doc.containers
        }
        return cls(spans=spans, elements=elements, containers=containers)

    def nearest_section_of(self, element_id: str) -> str | None:
        """元素 → 最近 heading/title 祖先（与 read_service 大纲锚同语义）.

        heading/title 元素锚定自身；父链断（重复 id/缺失）返回 None，
        不猜测标题文本匹配。
        """
        current = self.elements.get(element_id)
        if current is None:
            return None
        if current.element_type in _SECTION_ELEMENT_TYPES:
            return current.element_id
        parent_id = current.parent_id
        seen: set[str] = {element_id}
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = self.elements.get(parent_id)
            if parent is None:
                return None
            if parent.element_type in _SECTION_ELEMENT_TYPES:
                return parent.element_id
            parent_id = parent.parent_id
        return None


def _page_of_span(span: EvidenceSpan, index: _IrIndex) -> int | None:
    """span → 1 基页码。容器 page_number 优先；cell native_ref 的 page 与
    visual_region.page_index 均为 0 基容器序，+1 兜底。"""
    container = index.containers.get(span.page_id or "")
    if container is not None and container.page_number is not None:
        return int(container.page_number)
    native = span.native_ref or {}
    if isinstance(native.get("page"), int):
        return int(native["page"]) + 1
    region = span.visual_region or {}
    if isinstance(region.get("page_index"), int):
        return int(region["page_index"]) + 1
    return None


def _as_int(value: Any) -> int | None:
    """int 或数字字符串 → int（target_ref 解析产物是 str，IR json 是 int）."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _table_facts(rep: RetrievalRepresentation) -> tuple[str | None, int | None]:
    """table_row/table 表示 → (table_ref, row_index)。target_ref 形如
    ``{doc}#table_row:{table_ref}:{row}`` / ``{doc}#table:{table_ref}``."""
    if rep.representation_type == "table_row":
        table_ref = rep.container_ref
        marker = "#table_row:"
        if marker in rep.target_ref:
            tail = rep.target_ref.split(marker, 1)[1]
            head, _, row = tail.rpartition(":")
            table_ref = table_ref or (head or None)
            return table_ref, _as_int(row)
        return table_ref, None
    if rep.representation_type == "table":
        marker = "#table:"
        table_ref = rep.container_ref
        if marker in rep.target_ref:
            table_ref = table_ref or rep.target_ref.split(marker, 1)[1] or None
        return table_ref, None
    return None, None


def _description_for(native: Mapping[str, Any], source_format: str) -> str | None:
    """native 级位置说明（37 号决策 5：DOCX/PPTX 不冒充精确定位，给人读描述）."""
    paragraph = _as_int(native.get("paragraph_index"))
    if paragraph is not None:
        return f"第 {paragraph + 1} 段"
    table_index = _as_int(native.get("table_index"))
    row_index = _as_int(native.get("row_index"))
    if table_index is not None and row_index is not None:
        column = _as_int(native.get("column_index"))
        tail = f"第 {column + 1} 列" if column is not None else ""
        return f"表格 {table_index + 1} 第 {row_index + 1} 行{(' · ' + tail) if tail else ''}"
    if table_index is not None:
        return f"表格 {table_index + 1}"
    slide = _as_int(native.get("slide_index"))
    if slide is not None:
        return f"幻灯片 {slide + 1}"
    if source_format == "html" and native.get("xpath"):
        return None  # xpath 本身即机器可读位置，native_ref_json 承载
    return None


def _aggregate(
    rep: RetrievalRepresentation,
    index: _IrIndex,
    source_format: str,
) -> LocatorRecord:
    """聚合单元的 span 定位通道 → 单条 LocatorRecord."""
    document_ref = str(rep.facets.get("document") or "")
    section_path = rep.facets.get("section_path")
    table_ref, row_from_target = _table_facts(rep)

    span_ids: list[str] = []
    element_ids: list[str] = []
    for ref in rep.source_refs:
        element_id = ref.get("element_id")
        if isinstance(element_id, str) and element_id:
            element_ids.append(element_id)
        for span_id in ref.get("evidence_span_ids") or ():
            if isinstance(span_id, str) and span_id:
                span_ids.append(span_id)
    spans = [index.spans[sid] for sid in dict.fromkeys(span_ids) if sid in index.spans]

    pages = sorted({
        page for span in spans if (page := _page_of_span(span, index)) is not None
    })
    line_start = line_end = None
    for span in spans:
        locator = span.source_locator or {}
        start = _as_int(locator.get("line_start"))
        end = _as_int(locator.get("line_end"))
        if start is not None and (line_start is None or start < line_start):
            line_start = start
        if end is not None and (line_end is None or end > line_end):
            line_end = end
    sheet = cell = None
    native: dict[str, Any] | None = None
    for span in spans:
        ref = span.native_ref or {}
        if sheet is None and isinstance(ref.get("sheet"), str):
            sheet = ref["sheet"]
        if cell is None and isinstance(ref.get("cell"), str):
            cell = ref["cell"]
        if native is None and ref:
            native = dict(ref)

    # kind 优先级（38 号 §2.2）：page > line_range > sheet_cell > native
    if pages:
        kind = "page"
        page = pages[0]
    elif line_start is not None:
        kind = "line_range"
        page = None
    elif sheet is not None or cell is not None:
        kind = "sheet_cell"
        page = None
    elif native:
        kind = "native"
        page = None
    else:
        kind = "unavailable"
        page = None

    # 行号是"元素行区间并集"口径；kind 非 line_range 时不落行号字段
    #（避免 PDF 上出现伪造行语义的反向问题）。
    if kind != "line_range":
        line_start = line_end = None

    section_element_id: str | None = None
    for element_id in element_ids:
        section_element_id = index.nearest_section_of(element_id)
        if section_element_id is not None:
            break

    return LocatorRecord(
        representation_id=rep.representation_id,
        target_ref=rep.target_ref,
        document_ref=document_ref,
        source_format=source_format,
        locator_kind=kind,
        section_path=str(section_path) if section_path else None,
        section_element_id=section_element_id,
        page=page,
        line_start=line_start,
        line_end=line_end,
        sheet=sheet,
        cell=cell,
        table_ref=table_ref,
        row_index=row_from_target,
        native_ref=native or None,
        description=_description_for(native or {}, source_format) if kind == "native" else None,
    )


def extract_locator_records(
    doc: ParsedDocument,
    representations: Sequence[RetrievalRepresentation] | Iterable[RetrievalRepresentation],
) -> tuple[LocatorRecord, ...]:
    """IR + 检索表示 → 来源记录（纯函数）.

    - section/document 表示：恒 ``section_only``（L1 即可）；
    - alias 表示跳过（回 canonical 取位）；
    - 其余按 span 通道聚合，见 :func:`_aggregate`。
    """
    index = _IrIndex.of(doc)
    source_format = source_format_of(
        getattr(doc.source_identity, "parser_fingerprint", None)
    )
    records: list[LocatorRecord] = []
    for rep in representations:
        if rep.representation_type in ALIAS_REPRESENTATION_TYPES:
            continue
        if rep.representation_type in {"document", "section"}:
            records.append(LocatorRecord(
                representation_id=rep.representation_id,
                target_ref=rep.target_ref,
                document_ref=str(rep.facets.get("document") or ""),
                source_format=source_format,
                locator_kind="section_only",
                section_path=str(rep.facets.get("section_path") or "") or None,
                table_ref=None,
                row_index=None,
            ))
            continue
        records.append(_aggregate(rep, index, source_format))
    return tuple(records)


__all__ = [
    "ALIAS_REPRESENTATION_TYPES",
    "LOCATOR_VERSION",
    "LocatorRecord",
    "PRECISE_LOCATOR_KINDS",
    "extract_locator_records",
    "source_format_of",
]
