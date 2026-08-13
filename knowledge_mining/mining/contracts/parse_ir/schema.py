"""JSON Schema + referential-integrity validator for Parse IR v0.1.

Two-layer validation (SRS §4.7, C07):
  1. Structural: `jsonschema` checks the dict shape (field presence, types,
     enum membership) against `PARSE_IR_JSON_SCHEMA`.
  2. Referential: custom checks enforce that every cross-reference (relation
     endpoints, element parent, table caption / cell span, evidence span page)
     resolves to an entity declared in the same document. Dangling references
     are rejected (SRS §4.7 "关系悬空...不可进入质量门禁").

Per ADR-0003 D-001 we use `jsonschema` (already a project dependency) rather
than Pydantic.
"""
from __future__ import annotations

import jsonschema
from jsonschema import Draft7Validator

from knowledge_mining.mining.contracts.parse_ir.enums import (
    VALID_CONTAINER_TYPES,
    VALID_ELEMENT_TYPES,
    VALID_RELATION_TYPES,
)
from knowledge_mining.mining.contracts.parse_ir.types import (
    Confidence,
    EvidenceSpan,
    FigureAsset,
    FormulaAsset,
    ParsedDocument,
    TableAsset,
    ValidationIssue,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# JSON Schema (Draft 7) — structural shape for the top-level ParsedDocument.
#
# Intentionally permissive on optional fields (the frozen dataclasses carry the
# authoritative defaults); the schema's job is boundary validation: required
# fields present, enum membership correct, container/element/relation arrays
# well-typed. Deep referential checks live in `_check_referential_integrity`.
# ---------------------------------------------------------------------------

PARSE_IR_JSON_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Parse IR v0.1",
    "type": "object",
    "required": ["schema_version", "source_identity", "containers", "elements"],
    "properties": {
        "schema_version": {"type": "string"},
        "document_snapshot_id": {"type": "string"},
        "parse_run_id": {"type": "string"},
        "source_identity": {
            "type": "object",
            "required": ["source_raw_hash", "parser_fingerprint"],
            "properties": {
                "source_raw_hash": {"type": "string"},
                "parser_fingerprint": {"type": "string"},
                "parse_ir_schema_version": {"type": "string"},
                "normalizer_version": {"type": ["string", "null"]},
                "reconciler_version": {"type": ["string", "null"]},
                "dependency_fingerprint": {"type": ["string", "null"]},
            },
        },
        "containers": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["container_id", "container_type", "order_index"],
                "properties": {
                    "container_id": {"type": "string"},
                    "container_type": {
                        "type": "string",
                        "enum": sorted(VALID_CONTAINER_TYPES),
                    },
                    "order_index": {"type": "integer", "minimum": 0},
                    "parent_container_id": {"type": "string"},
                },
            },
        },
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["element_id", "element_type", "order_index"],
                "properties": {
                    "element_id": {"type": "string"},
                    "element_type": {
                        "type": "string",
                        "enum": sorted(VALID_ELEMENT_TYPES),
                    },
                    "order_index": {"type": "integer", "minimum": 0},
                    "parent_id": {"type": "string"},
                    "confidence": {"type": "object"},
                },
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "source_element_id", "target_element_id", "relation_type",
                ],
                "properties": {
                    "source_element_id": {"type": "string"},
                    "target_element_id": {"type": "string"},
                    "relation_type": {
                        "type": "string",
                        "enum": sorted(VALID_RELATION_TYPES),
                    },
                    "confidence": {"type": "number"},
                },
            },
        },
        "diagnostics": {"type": "object"},
        "metadata": {"type": "object"},
    },
}


_VALIDATOR = Draft7Validator(PARSE_IR_JSON_SCHEMA)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate(parsed: ParsedDocument) -> ValidationResult:
    """Validate a ParsedDocument structurally and referentially.

    Returns a :class:`ValidationResult`. ``valid`` is True iff no error-level
    issues were found. Warnings do not invalidate the document.
    """
    issues: list[ValidationIssue] = []

    # 1. Structural check via jsonschema on the serialized dict.
    as_dict = parsed.to_dict()
    for err in _VALIDATOR.iter_errors(as_dict):
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        # jsonschema enum errors carry the offending value; surface it.
        issues.append(ValidationIssue(
            level="error",
            code=_jsonschema_error_code(err),
            message=err.message,
            path=path,
        ))

    # 2. Referential-integrity + semantic checks on the typed objects.
    issues.extend(_check_referential_integrity(parsed))
    issues.extend(_check_confidence_ranges(parsed))
    issues.extend(_check_evidence_spans_nonempty(parsed))

    errors = [i for i in issues if i.level == "error"]
    return ValidationResult(
        valid=len(errors) == 0,
        issues=tuple(issues),
    )


# ---------------------------------------------------------------------------
# Referential integrity (SRS §4.7, §7.4)
# ---------------------------------------------------------------------------

def _check_referential_integrity(doc: ParsedDocument) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    element_ids: set[str] = {e.element_id for e in doc.elements}
    container_ids: set[str] = {c.container_id for c in doc.containers}

    # element.parent_id must point at an existing element.
    for e in doc.elements:
        if e.parent_id is not None and e.parent_id not in element_ids:
            issues.append(ValidationIssue(
                level="error",
                code="dangling_parent",
                message=(
                    f"element {e.element_id!r} parent_id {e.parent_id!r} "
                    "does not match any element"
                ),
                path=f"elements[{e.element_id}].parent_id",
            ))

    # relation endpoints must both exist.
    for r in doc.relations:
        if r.source_element_id not in element_ids:
            issues.append(ValidationIssue(
                level="error",
                code="dangling_relation",
                message=(
                    f"relation source {r.source_element_id!r} not found"
                ),
                path=f"relations[{r.source_element_id}->{r.target_element_id}]",
            ))
        if r.target_element_id not in element_ids:
            issues.append(ValidationIssue(
                level="error",
                code="dangling_relation",
                message=(
                    f"relation target {r.target_element_id!r} not found"
                ),
                path=f"relations[{r.source_element_id}->{r.target_element_id}]",
            ))

    # evidence span page_id must point at an existing container.
    # also collect all declared span ids for cell.source_span_id resolution.
    span_ids: set[str] = set()
    for e in doc.elements:
        for span in e.source_spans:
            span_ids.add(span.span_id)
            if span.page_id is not None and span.page_id not in container_ids:
                issues.append(ValidationIssue(
                    level="error",
                    code="dangling_span_page",
                    message=(
                        f"evidence span {span.span_id!r} page_id "
                        f"{span.page_id!r} does not match any container"
                    ),
                    path=f"elements[{e.element_id}].source_spans[{span.span_id}].page_id",
                ))

    # structured asset references.
    for aid, asset in doc.structured_assets.items():
        if isinstance(asset, TableAsset):
            issues.extend(_check_table_asset(aid, asset, element_ids, span_ids))
        elif isinstance(asset, FigureAsset):
            if asset.caption_element_id and asset.caption_element_id not in element_ids:
                issues.append(ValidationIssue(
                    level="error",
                    code="dangling_caption_ref",
                    message=(
                        f"figure {aid!r} caption_element_id "
                        f"{asset.caption_element_id!r} not found"
                    ),
                    path=f"structured_assets[{aid}].caption_element_id",
                ))
        elif isinstance(asset, FormulaAsset):
            pass  # formula has no cross-element refs in v0.1

    return issues


def _check_table_asset(
    aid: str, asset: TableAsset, element_ids: set[str], span_ids: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if asset.caption_element_id and asset.caption_element_id not in element_ids:
        issues.append(ValidationIssue(
            level="error",
            code="dangling_caption_ref",
            message=(
                f"table {aid!r} caption_element_id "
                f"{asset.caption_element_id!r} not found"
            ),
            path=f"structured_assets[{aid}].caption_element_id",
        ))
    for fn_id in asset.footnote_element_ids:
        if fn_id not in element_ids:
            issues.append(ValidationIssue(
                level="error",
                code="dangling_footnote_ref",
                message=(
                    f"table {aid!r} footnote_element_id {fn_id!r} not found"
                ),
                path=f"structured_assets[{aid}].footnote_element_ids",
            ))
    # cell.source_span_id must resolve to a span declared on some element.
    # v0.1 scopes spans to elements; container-level spans are out of scope.
    for cell in asset.cells:
        if cell.source_span_id and cell.source_span_id not in span_ids:
            issues.append(ValidationIssue(
                level="error",
                code="dangling_cell_span",
                message=(
                    f"table {aid!r} cell[{cell.row_index},{cell.column_index}] "
                    f"source_span_id {cell.source_span_id!r} not found"
                ),
                path=f"structured_assets[{aid}].cells",
            ))
    return issues


def _check_confidence_ranges(doc: ParsedDocument) -> list[ValidationIssue]:
    """Confidence dimensions, when present, must be in [0.0, 1.0]."""
    issues: list[ValidationIssue] = []
    to_check: list[tuple[Confidence, str]] = []
    for e in doc.elements:
        to_check.append((e.confidence, f"elements[{e.element_id}].confidence"))
    for aid, asset in doc.structured_assets.items():
        if isinstance(asset, (TableAsset, FormulaAsset)):
            to_check.append((asset.confidence, f"structured_assets[{aid}].confidence"))

    for conf, path in to_check:
        for dim in ("text", "layout", "type", "reading_order"):
            val = getattr(conf, dim)
            if val is not None and not (0.0 <= val <= 1.0):
                issues.append(ValidationIssue(
                    level="error",
                    code="confidence_out_of_range",
                    message=(
                        f"confidence.{dim}={val} out of [0.0, 1.0] at {path}"
                    ),
                    path=f"{path}.{dim}",
                ))
    return issues


def _check_evidence_spans_nonempty(doc: ParsedDocument) -> list[ValidationIssue]:
    """An evidence span must carry at least one locator (SRS §7.4 do not fabricate)."""
    issues: list[ValidationIssue] = []
    for e in doc.elements:
        for span in e.source_spans:
            if not _span_has_locator(span):
                issues.append(ValidationIssue(
                    level="error",
                    code="empty_evidence_span",
                    message=(
                        f"evidence span {span.span_id!r} on element "
                        f"{e.element_id!r} has no locator (text_range/"
                        "source_locator/visual_region/native_ref/raw_text)"
                    ),
                    path=f"elements[{e.element_id}].source_spans[{span.span_id}]",
                ))
    return issues


def _span_has_locator(span: EvidenceSpan) -> bool:
    return any((
        span.text_range is not None,
        span.source_locator is not None,
        span.visual_region is not None,
        span.native_ref is not None,
        span.raw_text is not None,
    ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jsonschema_error_code(err: jsonschema.exceptions.ValidationError) -> str:
    """Map a jsonschema error to a stable machine code based on the validator."""
    validator = err.validator
    if validator == "required":
        return "missing_required_field"
    if validator == "enum":
        # Distinguish element/relation/container enum errors for readability.
        schema_path = ".".join(str(p) for p in err.schema_path)
        if "element_type" in schema_path:
            return "invalid_element_type"
        if "relation_type" in schema_path:
            return "invalid_relation_type"
        if "container_type" in schema_path:
            return "invalid_container_type"
        return "invalid_enum_value"
    if validator == "type":
        return "type_mismatch"
    if validator == "minimum":
        return "value_below_minimum"
    return f"schema_error_{validator}"


__all__ = [
    "PARSE_IR_JSON_SCHEMA",
    "validate",
]
