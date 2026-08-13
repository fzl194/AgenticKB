"""Parse IR v0.1 contract — frozen dataclass types + jsonschema validator.

This package implements the Parse IR data contract defined in
`docs/文档解析平台化-能力规格与工作拆解.md` §5 (a worked example), §7 (functional
contract), §3.6-§3.9 (concept boundaries) and acceptance scenario §A01.

Design decisions (see ADR-0003):
  * D-001 — frozen dataclass + frozenset constants, NOT Pydantic. Validation
    uses ``jsonschema`` (already a project dependency) plus a custom
    referential-integrity pass.
  * D-007..D-NNN — field-level tradeoffs are logged in ADR-0003.

Layer rule (SRS §2.1, C07): this module depends only on the stdlib and
``jsonschema``. It MUST NOT import business layers, the database, or FastAPI.
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parse_ir.enums import (
    PARSE_IR_SCHEMA_VERSION,
    VALID_ARTIFACT_CLASSES,
    VALID_CONFIDENCE_DIMENSIONS,
    VALID_CONTAINER_TYPES,
    VALID_ELEMENT_TYPES,
    VALID_RELATION_TYPES,
)
from knowledge_mining.mining.contracts.parse_ir.ids import stable_element_id
from knowledge_mining.mining.contracts.parse_ir.schema import (
    PARSE_IR_JSON_SCHEMA,
    validate,
)
from knowledge_mining.mining.contracts.parse_ir.types import (
    Confidence,
    Container,
    Diagnostics,
    Element,
    EvidenceSpan,
    FigureAsset,
    FormulaAsset,
    ParseIdentity,
    ParsedDocument,
    Relation,
    TableAsset,
    TableCell,
    ValidationIssue,
    ValidationResult,
)

__all__ = [
    # enums / constants
    "PARSE_IR_SCHEMA_VERSION",
    "PARSE_IR_JSON_SCHEMA",
    "VALID_ARTIFACT_CLASSES",
    "VALID_CONFIDENCE_DIMENSIONS",
    "VALID_CONTAINER_TYPES",
    "VALID_ELEMENT_TYPES",
    "VALID_RELATION_TYPES",
    # ids
    "stable_element_id",
    # types
    "Confidence",
    "Container",
    "Diagnostics",
    "Element",
    "EvidenceSpan",
    "FigureAsset",
    "FormulaAsset",
    "ParseIdentity",
    "ParsedDocument",
    "Relation",
    "TableAsset",
    "TableCell",
    "ValidationIssue",
    "ValidationResult",
    # validation
    "validate",
]
