"""Frozen dataclass types for Parse IR v0.1 (SRS §5/§7, ADR-0003 D-001).

Style mirrors `knowledge_mining.mining.contracts.models`: stdlib only,
`@dataclass(frozen=True)`, `VALID_*` frozenset constants, `tuple[...]` for
immutable child collections, `dict[str, Any]` via `field(default_factory=dict)`.

Design rules for missing data (SRS §7.4 "未知可缺，不得伪造"):
- Optional structural fields default to ``None``.
- Optional numeric confidence dimensions default to ``None`` (NOT 0.0).
- Locator dicts (source_locator / visual_region / native_ref) default to
  ``None`` when the backend has no such evidence; the validator only checks
  referential integrity for fields that are present.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from knowledge_mining.mining.contracts.parse_ir.enums import PARSE_IR_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Confidence (SRS §5.3, §7.3) — multi-dimensional
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Confidence:
    """Per-dimension recognition confidence. None == unknown (not fabricated).

    SRS §5.3: confidence is multi-dimensional (text recognition, layout, type
    classification, reading order). A single scalar is intentionally rejected
    by the contract so that downstream quality gates can reason per-dimension.
    """

    text: float | None = None
    layout: float | None = None
    type: float | None = None  # noqa: A003 (shadows builtin; matches SRS term)
    reading_order: float | None = None
    # Origin label: which backend/method produced these scores (e.g. "docling",
    # "ocr", "native"). Free-form string; enables provenance without a new enum.
    source: str = "unknown"


# ---------------------------------------------------------------------------
# Evidence Span (SRS §3.8, §7.4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceSpan:
    """Where an element/cell is located in the original file.

    SRS §7.4 distinguishes four locator kinds; each is independently optional:
      * ``text_range`` — character range within the owning element's text,
        as ``(char_start, char_end)`` (end-exclusive).
      * ``source_locator`` — original-file locator (line range, OOXML ref, ...).
      * ``visual_region`` — bbox/polygon on a page/slide.
      * ``native_ref`` — sheet cell, DOM path, OOXML object id, etc.

    A span MUST carry at least one locator or ``raw_text``; a span with every
    field absent is rejected by the validator (would be a fabricated evidence).
    """

    span_id: str
    page_id: str | None = None
    text_range: tuple[int, int] | None = None
    source_locator: dict[str, Any] | None = None
    visual_region: dict[str, Any] | None = None
    native_ref: dict[str, Any] | None = None
    raw_text: str | None = None
    ocr_confidence: float | None = None


# ---------------------------------------------------------------------------
# Relation (SRS §7.5)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Relation:
    """A deterministic document-structure relation between two elements.

    SRS §7.5 v0.1 relation set: parent_of, contains, next_in_reading_order,
    caption_of, footnote_of, continues_on, references, anchored_at,
    derived_from. Domain/semantic relations are out of scope.
    """

    source_element_id: str
    target_element_id: str
    relation_type: str
    confidence: float = 1.0
    method: str = "unknown"
    provenance: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Element (SRS §3.7, §7.3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Element:
    """An atomic, independently locatable document element.

    SRS §3.7: element is the atomic structural unit, NOT defined by token size
    (a long paragraph may still be one element; splitting is Segment Compiler's
    job). parent_id points at the enclosing element (heading) when known.
    """

    element_id: str
    element_type: str
    order_index: int
    text: str = ""
    normalized_text: str = ""
    parent_id: str | None = None
    page_span_ids: tuple[str, ...] = ()
    source_spans: tuple[EvidenceSpan, ...] = ()
    style: dict[str, Any] = field(default_factory=dict)
    confidence: Confidence = field(default_factory=Confidence)
    parser_annotations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Container (SRS §3.6, §7.2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Container:
    """A native content container (page / sheet / slide / topic / ...).

    SRS §7.2: hierarchy is allowed (workbook -> sheet, archive -> topic) via
    parent_container_id. Containers do NOT fake page numbers when a format has
    none (SRS §3.6).
    """

    container_id: str
    container_type: str
    order_index: int
    name: str | None = None
    page_number: int | None = None
    width: float | None = None
    height: float | None = None
    coordinate_unit: str | None = None
    rotation: float | None = None
    rendered_image_ref: str | None = None
    text_layer_kind: str | None = None
    language: str | None = None
    quality: float | None = None
    parent_container_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Table Asset (SRS §3.9, §5.2, §7.6)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableCell:
    """One table cell. Preserves raw text; normalized value is optional.

    SRS §7.6: cell value MAY have a normalized type, but raw text is always
    kept; formula value and display value are separate fields.
    """

    row_index: int
    column_index: int
    text: str = ""
    row_span: int = 1
    column_span: int = 1
    normalized_value: str | None = None
    value_type: str | None = None
    formula: str | None = None
    is_header: bool = False
    source_span_id: str | None = None


@dataclass(frozen=True)
class TableAsset:
    """Structured asset: a table grid with cells, headers and continuation.

    SRS §7.6: must express row/column counts, cells, row/column spans, header
    regions (as ``(row_start, row_end)`` inclusive ranges), caption, footnotes,
    cross-page continuation and per-cell source spans.
    """

    table_id: str
    page_span_ids: tuple[str, ...]
    rows: int
    columns: int
    cells: tuple[TableCell, ...]
    header_regions: tuple[tuple[int, int], ...] = ()
    caption_element_id: str | None = None
    footnote_element_ids: tuple[str, ...] = ()
    continuation_of: str | None = None
    confidence: Confidence = field(default_factory=Confidence)


# ---------------------------------------------------------------------------
# Figure / Chart Asset (SRS §7.7)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FigureAsset:
    """Structured asset: a figure/chart with original image and derived data.

    SRS §7.7: must express original/crop image refs, region, caption and
    in-text mentions. VLM descriptions, chart series and OCR text are
    ``derived_annotations`` and MUST NOT overwrite the original image or
    the original caption element.
    """

    figure_id: str
    original_image_ref: str | None = None
    crop_ref: str | None = None
    region: dict[str, Any] | None = None
    caption_element_id: str | None = None
    mention_element_ids: tuple[str, ...] = ()
    image_hash: str | None = None
    derived_annotations: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Formula Asset (SRS §7.8)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FormulaAsset:
    """Structured asset: a mathematical formula.

    SRS §7.8: must express visual region, raw OCR, LaTeX/MathML (when
    available), inline vs block display, confidence and parser provenance.
    """

    formula_id: str
    display: str = "block"  # "inline" | "block"
    visual_region: dict[str, Any] | None = None
    raw_ocr: str | None = None
    latex: str | None = None
    mathml: str | None = None
    confidence: Confidence = field(default_factory=Confidence)
    provenance: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Diagnostics (SRS §7.1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Diagnostics:
    """Parser self-report: versions, warnings and errors for this parse run."""

    schema_version: str = PARSE_IR_SCHEMA_VERSION
    parser_name: str | None = None
    parser_version: str | None = None
    model_versions: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    backend_provenance: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parse Identity (SRS §3.5)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParseIdentity:
    """Fingerprint inputs for a parsed document (SRS §3.5, ADR-0001 O1).

    Used by the Snapshot layer to compute ``snapshot_fingerprint``. Per ADR
    decision O1, ``document_id`` is NOT part of the fingerprint (cross-document
    snapshot reuse via snapshot_links).
    """

    source_raw_hash: str
    parser_fingerprint: str
    parse_ir_schema_version: str = PARSE_IR_SCHEMA_VERSION
    normalizer_version: str | None = None
    reconciler_version: str | None = None
    dependency_fingerprint: str | None = None
    rule_config_fingerprint: str | None = None


# ---------------------------------------------------------------------------
# Parsed Document (SRS §7.1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedDocument:
    """The top-level Parse IR artifact for one parse run.

    SRS §7.1: must contain schema version, source identity, parse identity,
    document metadata, containers, elements, relations, assets and
    diagnostics. MUST NOT contain retrieval topK, embeddings, domain
    entities, ontology types or scenario summaries (downstream concerns).
    """

    schema_version: str
    source_identity: ParseIdentity
    containers: tuple[Container, ...]
    elements: tuple[Element, ...]
    diagnostics: Diagnostics = field(default_factory=Diagnostics)
    relations: tuple[Relation, ...] = ()
    binary_assets: dict[str, Any] = field(default_factory=dict)
    structured_assets: dict[str, Any] = field(default_factory=dict)
    document_snapshot_id: str | None = None
    parse_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- Serialization (round-trip) -----------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParsedDocument:
        """Reconstruct a ParsedDocument from a plain dict (JSON-deserialized)."""
        return _parsed_document_from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return _parsed_document_to_dict(self)


# ---------------------------------------------------------------------------
# Validation result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationIssue:
    """One issue found during validation.

    Attributes:
        level: "error" (blocks the document) or "warning" (advisory).
        code: Stable machine code, e.g. "dangling_relation".
        message: Human-readable explanation.
        path: Dotted path to the offending field/value.
    """

    level: str  # "error" | "warning"
    code: str
    message: str
    path: str = ""


@dataclass(frozen=True)
class ValidationResult:
    """Aggregate validation outcome. ``valid`` is True iff no error-level issues."""

    valid: bool
    issues: tuple[ValidationIssue, ...] = ()


# ---------------------------------------------------------------------------
# Serialization helpers
#
# These convert between the frozen dataclasses and plain dicts. Kept small and
# explicit (no reflection) so the round-trip is auditable. Structured assets
# are tagged with a ``kind`` discriminator so the right dataclass is rebuilt.
# ---------------------------------------------------------------------------

_STRUCTURED_KIND_TO_FACTORY: dict[str, Any] = {
    "table": TableAsset,
    "figure": FigureAsset,
    "formula": FormulaAsset,
}

_STRUCTURED_TYPE_OF: dict[type, str] = {
    v: k for k, v in _STRUCTURED_KIND_TO_FACTORY.items()
}


def _confidence_to_dict(c: Confidence) -> dict[str, Any]:
    return {
        "text": c.text,
        "layout": c.layout,
        "type": c.type,
        "reading_order": c.reading_order,
        "source": c.source,
    }


def _confidence_from_dict(d: dict[str, Any]) -> Confidence:
    return Confidence(
        text=d.get("text"),
        layout=d.get("layout"),
        type=d.get("type"),
        reading_order=d.get("reading_order"),
        source=d.get("source", "unknown"),
    )


def _span_to_dict(s: EvidenceSpan) -> dict[str, Any]:
    out: dict[str, Any] = {"span_id": s.span_id}
    if s.page_id is not None:
        out["page_id"] = s.page_id
    if s.text_range is not None:
        out["text_range"] = [s.text_range[0], s.text_range[1]]
    if s.source_locator is not None:
        out["source_locator"] = s.source_locator
    if s.visual_region is not None:
        out["visual_region"] = s.visual_region
    if s.native_ref is not None:
        out["native_ref"] = s.native_ref
    if s.raw_text is not None:
        out["raw_text"] = s.raw_text
    if s.ocr_confidence is not None:
        out["ocr_confidence"] = s.ocr_confidence
    return out


def _span_from_dict(d: dict[str, Any]) -> EvidenceSpan:
    tr = d.get("text_range")
    return EvidenceSpan(
        span_id=d["span_id"],
        page_id=d.get("page_id"),
        text_range=(tr[0], tr[1]) if tr is not None else None,
        source_locator=d.get("source_locator"),
        visual_region=d.get("visual_region"),
        native_ref=d.get("native_ref"),
        raw_text=d.get("raw_text"),
        ocr_confidence=d.get("ocr_confidence"),
    )


def _element_to_dict(e: Element) -> dict[str, Any]:
    out: dict[str, Any] = {
        "element_id": e.element_id,
        "element_type": e.element_type,
        "order_index": e.order_index,
    }
    if e.parent_id is not None:
        out["parent_id"] = e.parent_id
    out["text"] = e.text
    out["normalized_text"] = e.normalized_text
    out["page_span_ids"] = list(e.page_span_ids)
    out["source_spans"] = [_span_to_dict(s) for s in e.source_spans]
    out["style"] = dict(e.style)
    out["confidence"] = _confidence_to_dict(e.confidence)
    out["parser_annotations"] = dict(e.parser_annotations)
    out["metadata"] = dict(e.metadata)
    return out


def _element_from_dict(d: dict[str, Any]) -> Element:
    return Element(
        element_id=d["element_id"],
        element_type=d["element_type"],
        order_index=d["order_index"],
        text=d.get("text", ""),
        normalized_text=d.get("normalized_text", ""),
        parent_id=d.get("parent_id"),
        page_span_ids=tuple(d.get("page_span_ids", [])),
        source_spans=tuple(_span_from_dict(s) for s in d.get("source_spans", [])),
        style=dict(d.get("style", {})),
        confidence=_confidence_from_dict(d.get("confidence", {})),
        parser_annotations=dict(d.get("parser_annotations", {})),
        metadata=dict(d.get("metadata", {})),
    )


def _container_to_dict(c: Container) -> dict[str, Any]:
    out: dict[str, Any] = {
        "container_id": c.container_id,
        "container_type": c.container_type,
        "order_index": c.order_index,
    }
    for opt in (
        "name", "page_number", "width", "height", "coordinate_unit", "rotation",
        "rendered_image_ref", "text_layer_kind", "language", "quality",
        "parent_container_id",
    ):
        v = getattr(c, opt)
        if v is not None:
            out[opt] = v
    out["metadata"] = dict(c.metadata)
    return out


def _container_from_dict(d: dict[str, Any]) -> Container:
    return Container(
        container_id=d["container_id"],
        container_type=d["container_type"],
        order_index=d["order_index"],
        name=d.get("name"),
        page_number=d.get("page_number"),
        width=d.get("width"),
        height=d.get("height"),
        coordinate_unit=d.get("coordinate_unit"),
        rotation=d.get("rotation"),
        rendered_image_ref=d.get("rendered_image_ref"),
        text_layer_kind=d.get("text_layer_kind"),
        language=d.get("language"),
        quality=d.get("quality"),
        parent_container_id=d.get("parent_container_id"),
        metadata=dict(d.get("metadata", {})),
    )


def _relation_to_dict(r: Relation) -> dict[str, Any]:
    return {
        "source_element_id": r.source_element_id,
        "target_element_id": r.target_element_id,
        "relation_type": r.relation_type,
        "confidence": r.confidence,
        "method": r.method,
        "provenance": dict(r.provenance),
    }


def _relation_from_dict(d: dict[str, Any]) -> Relation:
    return Relation(
        source_element_id=d["source_element_id"],
        target_element_id=d["target_element_id"],
        relation_type=d["relation_type"],
        confidence=d.get("confidence", 1.0),
        method=d.get("method", "unknown"),
        provenance=dict(d.get("provenance", {})),
    )


def _cell_from_dict(d: dict[str, Any]) -> TableCell:
    return TableCell(
        row_index=d["row_index"],
        column_index=d["column_index"],
        text=d.get("text", ""),
        row_span=d.get("row_span", 1),
        column_span=d.get("column_span", 1),
        normalized_value=d.get("normalized_value"),
        value_type=d.get("value_type"),
        formula=d.get("formula"),
        is_header=d.get("is_header", False),
        source_span_id=d.get("source_span_id"),
    )


def _structured_from_dict(kind: str, d: dict[str, Any]) -> Any:
    if kind == "table":
        return TableAsset(
            table_id=d["table_id"],
            page_span_ids=tuple(d.get("page_span_ids", [])),
            rows=d["rows"],
            columns=d["columns"],
            cells=tuple(_cell_from_dict(c) for c in d.get("cells", [])),
            header_regions=tuple(tuple(r) for r in d.get("header_regions", [])),
            caption_element_id=d.get("caption_element_id"),
            footnote_element_ids=tuple(d.get("footnote_element_ids", [])),
            continuation_of=d.get("continuation_of"),
            confidence=_confidence_from_dict(d.get("confidence", {})),
        )
    if kind == "figure":
        return FigureAsset(
            figure_id=d["figure_id"],
            original_image_ref=d.get("original_image_ref"),
            crop_ref=d.get("crop_ref"),
            region=d.get("region"),
            caption_element_id=d.get("caption_element_id"),
            mention_element_ids=tuple(d.get("mention_element_ids", [])),
            image_hash=d.get("image_hash"),
            derived_annotations=dict(d.get("derived_annotations", {})),
        )
    if kind == "formula":
        return FormulaAsset(
            formula_id=d["formula_id"],
            display=d.get("display", "block"),
            visual_region=d.get("visual_region"),
            raw_ocr=d.get("raw_ocr"),
            latex=d.get("latex"),
            mathml=d.get("mathml"),
            confidence=_confidence_from_dict(d.get("confidence", {})),
            provenance=dict(d.get("provenance", {})),
        )
    raise ValueError(f"unknown structured asset kind: {kind}")


def _structured_to_dict(obj: Any) -> dict[str, Any]:
    kind = _STRUCTURED_TYPE_OF.get(type(obj))
    if kind is None:
        raise ValueError(f"unknown structured asset type: {type(obj)}")
    if kind == "table":
        t: TableAsset = obj
        return {
            "kind": "table",
            "table_id": t.table_id,
            "caption_element_id": t.caption_element_id,
            "page_span_ids": list(t.page_span_ids),
            "rows": t.rows,
            "columns": t.columns,
            "cells": [
                {
                    "row_index": c.row_index,
                    "column_index": c.column_index,
                    "row_span": c.row_span,
                    "column_span": c.column_span,
                    "text": c.text,
                    "normalized_value": c.normalized_value,
                    "value_type": c.value_type,
                    "formula": c.formula,
                    "is_header": c.is_header,
                    "source_span_id": c.source_span_id,
                }
                for c in t.cells
            ],
            "header_regions": [list(r) for r in t.header_regions],
            "footnote_element_ids": list(t.footnote_element_ids),
            "continuation_of": t.continuation_of,
            "confidence": _confidence_to_dict(t.confidence),
        }
    if kind == "figure":
        f: FigureAsset = obj
        return {
            "kind": "figure",
            "figure_id": f.figure_id,
            "original_image_ref": f.original_image_ref,
            "crop_ref": f.crop_ref,
            "region": f.region,
            "caption_element_id": f.caption_element_id,
            "mention_element_ids": list(f.mention_element_ids),
            "image_hash": f.image_hash,
            "derived_annotations": dict(f.derived_annotations),
        }
    # formula
    fm: FormulaAsset = obj
    return {
        "kind": "formula",
        "formula_id": fm.formula_id,
        "display": fm.display,
        "visual_region": fm.visual_region,
        "raw_ocr": fm.raw_ocr,
        "latex": fm.latex,
        "mathml": fm.mathml,
        "confidence": _confidence_to_dict(fm.confidence),
        "provenance": dict(fm.provenance),
    }


def _parsed_document_to_dict(doc: ParsedDocument) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": doc.schema_version,
        "source_identity": {
            "source_raw_hash": doc.source_identity.source_raw_hash,
            "parser_fingerprint": doc.source_identity.parser_fingerprint,
            "parse_ir_schema_version": doc.source_identity.parse_ir_schema_version,
            "normalizer_version": doc.source_identity.normalizer_version,
            "reconciler_version": doc.source_identity.reconciler_version,
            "dependency_fingerprint": doc.source_identity.dependency_fingerprint,
            "rule_config_fingerprint": doc.source_identity.rule_config_fingerprint,
        },
    }
    if doc.document_snapshot_id is not None:
        out["document_snapshot_id"] = doc.document_snapshot_id
    if doc.parse_run_id is not None:
        out["parse_run_id"] = doc.parse_run_id
    out["containers"] = [_container_to_dict(c) for c in doc.containers]
    out["elements"] = [_element_to_dict(e) for e in doc.elements]
    out["relations"] = [_relation_to_dict(r) for r in doc.relations]
    out["binary_assets"] = dict(doc.binary_assets)
    out["structured_assets"] = {
        aid: _structured_to_dict(obj) for aid, obj in doc.structured_assets.items()
    }
    out["diagnostics"] = {
        "schema_version": doc.diagnostics.schema_version,
        "parser_name": doc.diagnostics.parser_name,
        "parser_version": doc.diagnostics.parser_version,
        "model_versions": dict(doc.diagnostics.model_versions),
        "warnings": list(doc.diagnostics.warnings),
        "errors": list(doc.diagnostics.errors),
        "backend_provenance": dict(doc.diagnostics.backend_provenance),
    }
    out["metadata"] = dict(doc.metadata)
    return out


def _parsed_document_from_dict(data: dict[str, Any]) -> ParsedDocument:
    si = data["source_identity"]
    structured: dict[str, Any] = {}
    for aid, sd in data.get("structured_assets", {}).items():
        kind = sd.get("kind")
        if kind is None:
            raise ValueError(f"structured asset {aid!r} missing 'kind' discriminator")
        structured[aid] = _structured_from_dict(kind, sd)

    diag = data.get("diagnostics", {})
    return ParsedDocument(
        schema_version=data["schema_version"],
        document_snapshot_id=data.get("document_snapshot_id"),
        parse_run_id=data.get("parse_run_id"),
        source_identity=ParseIdentity(
            source_raw_hash=si["source_raw_hash"],
            parser_fingerprint=si["parser_fingerprint"],
            parse_ir_schema_version=si.get(
                "parse_ir_schema_version", PARSE_IR_SCHEMA_VERSION,
            ),
            normalizer_version=si.get("normalizer_version"),
            reconciler_version=si.get("reconciler_version"),
            dependency_fingerprint=si.get("dependency_fingerprint"),
            rule_config_fingerprint=si.get("rule_config_fingerprint"),
        ),
        containers=tuple(_container_from_dict(c) for c in data.get("containers", [])),
        elements=tuple(_element_from_dict(e) for e in data.get("elements", [])),
        relations=tuple(_relation_from_dict(r) for r in data.get("relations", [])),
        binary_assets=dict(data.get("binary_assets", {})),
        structured_assets=structured,
        diagnostics=Diagnostics(
            schema_version=diag.get("schema_version", PARSE_IR_SCHEMA_VERSION),
            parser_name=diag.get("parser_name"),
            parser_version=diag.get("parser_version"),
            model_versions=dict(diag.get("model_versions", {})),
            warnings=tuple(diag.get("warnings", [])),
            errors=tuple(diag.get("errors", [])),
            backend_provenance=dict(diag.get("backend_provenance", {})),
        ),
        metadata=dict(data.get("metadata", {})),
    )


__all__ = [
    "Confidence",
    "EvidenceSpan",
    "Relation",
    "Element",
    "Container",
    "TableCell",
    "TableAsset",
    "FigureAsset",
    "FormulaAsset",
    "Diagnostics",
    "ParseIdentity",
    "ParsedDocument",
    "ValidationIssue",
    "ValidationResult",
    "replace",
]
