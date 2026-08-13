"""Enum-adjacent constants for Parse IR v0.1 (SRS §5/§7, ADR-0003 D-001).

Frozen `frozenset` constants mirror the style of `contracts.models.VALID_*`.
These are the single source of truth for accepted member strings; the JSON
Schema and the validator reference them.
"""
from __future__ import annotations


# Parse IR schema version this contract describes (SRS §7.1, ADR-0001 O1).
PARSE_IR_SCHEMA_VERSION: str = "0.1.0"

# --- Containers (SRS §3.6, §7.2) -------------------------------------------
# Native content containers per format family. The validator only checks
# membership; hierarchy (e.g. workbook -> sheet) is expressed via
# Container.parent_container_id, not via separate enum values.
VALID_CONTAINER_TYPES: frozenset[str] = frozenset({
    "page",
    "section",
    "slide",
    "sheet",
    "workbook",
    "topic",
    "dom_document",
    "message",
    "attachment",
})

# --- Elements (SRS §5.3, §7.3) ----------------------------------------------
# First-version element type set. `unknown` is the explicit fallback so that
# parsers never need to fabricate a wrong type (SRS §7.4 "未知可缺，不得伪造").
VALID_ELEMENT_TYPES: frozenset[str] = frozenset({
    "title",
    "heading",
    "paragraph",
    "list",
    "list_item",
    "code",
    "quote",
    "table",
    "figure",
    "chart",
    "formula",
    "form_field",
    "key_value",
    "footnote",
    "page_header",
    "page_footer",
    "page_number",
    "toc_entry",
    "caption",
    "reference",
    "signature",
    "selection_mark",
    "unknown",
})

# --- Relations (SRS §7.5) ---------------------------------------------------
# Only deterministic document-structure relations in v0.1. Domain/semantic
# relations are explicitly out of scope (SRS §2.1 "解析不做领域推理").
VALID_RELATION_TYPES: frozenset[str] = frozenset({
    "parent_of",
    "contains",
    "next_in_reading_order",
    "caption_of",
    "footnote_of",
    "continues_on",
    "references",
    "anchored_at",
    "derived_from",
})

# --- Artifact classes (SRS §3.1A, §8.1) -------------------------------------
# Storage Object artifact_class values referenced by Parse IR provenance.
VALID_ARTIFACT_CLASSES: frozenset[str] = frozenset({
    "source",
    "backend_raw",
    "parse_ir",
    "page_render",
    "binary_asset",
    "temporary",
})

# --- Confidence dimensions (SRS §5.3) ---------------------------------------
# Confidence is multi-dimensional, not a single float. Each dimension is
# independently optional (unknown => None, never fabricated).
VALID_CONFIDENCE_DIMENSIONS: frozenset[str] = frozenset({
    "text",
    "layout",
    "type",
    "reading_order",
})
