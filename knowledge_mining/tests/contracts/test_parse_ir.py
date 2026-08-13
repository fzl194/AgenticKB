"""Pytest suite for the Parse IR v0.1 contract (WP0.2).

References:
- SRS §5/§7/A01 (field semantics)
- ADR-0003 D-001 (frozen dataclass + jsonschema), D-007+ (field tradeoffs)
- ADR-0002 O2 (granularity)
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest

from knowledge_mining.mining.contracts.parse_ir import (
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
    stable_element_id,
    validate,
)
from knowledge_mining.mining.contracts.parse_ir.enums import (
    PARSE_IR_SCHEMA_VERSION,
    VALID_ARTIFACT_CLASSES,
    VALID_CONFIDENCE_DIMENSIONS,
    VALID_CONTAINER_TYPES,
    VALID_ELEMENT_TYPES,
    VALID_RELATION_TYPES,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers / minimal fixtures
# ---------------------------------------------------------------------------

def _identity() -> ParseIdentity:
    return ParseIdentity(
        source_raw_hash="sha256:abc",
        parser_fingerprint="legacy@1.0.0",
        parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
    )


def _container() -> Container:
    return Container(
        container_id="c1",
        container_type="dom_document",
        order_index=0,
    )


def _span(span_id: str, page_id: str = "c1") -> EvidenceSpan:
    return EvidenceSpan(span_id=span_id, page_id=page_id, text_range=(0, 5))


def _element(
    eid: str, etype: str = "paragraph", parent: str | None = None, order: int = 0,
) -> Element:
    return Element(
        element_id=eid,
        element_type=etype,
        parent_id=parent,
        order_index=order,
        text=f"text-{eid}",
        normalized_text=f"text-{eid}",
        page_span_ids=("c1",),
        source_spans=(_span(f"s-{eid}"),),
    )


def _minimal_doc() -> ParsedDocument:
    """1 container + 2 elements + 1 relation — minimal valid document."""
    e1 = _element("e1", "heading", order=0)
    e2 = _element("e2", "paragraph", parent="e1", order=1)
    rel = Relation(
        source_element_id="e1",
        target_element_id="e2",
        relation_type="next_in_reading_order",
        confidence=1.0,
        method="order_rule",
    )
    return ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=_identity(),
        containers=(_container(),),
        elements=(e1, e2),
        relations=(rel,),
        diagnostics=Diagnostics(schema_version=PARSE_IR_SCHEMA_VERSION),
    )


# ---------------------------------------------------------------------------
# 1. Minimal valid document passes
# ---------------------------------------------------------------------------

class TestMinimalDocument:
    def test_minimal_document_validates(self):
        doc = _minimal_doc()
        result = validate(doc)
        assert result.valid, f"unexpected issues: {result.issues}"

    def test_is_frozen_dataclass(self):
        assert is_dataclass(ParsedDocument)
        from dataclasses import fields
        # frozen=True is set on the class
        assert ParsedDocument.__dataclass_params__.frozen is True


# ---------------------------------------------------------------------------
# 2. Referential integrity
# ---------------------------------------------------------------------------

class TestReferentialIntegrity:
    def test_dangling_relation_rejected(self):
        doc = _minimal_doc()
        bad_rel = Relation(
            source_element_id="e1",
            target_element_id="ghost",  # does not exist
            relation_type="next_in_reading_order",
            confidence=1.0,
            method="order_rule",
        )
        doc = _replace(doc, relations=(bad_rel,))
        result = validate(doc)
        assert not result.valid
        assert any(i.code == "dangling_relation" and i.level == "error" for i in result.issues)

    def test_dangling_parent_id_rejected(self):
        e2 = _element("e2", "paragraph", parent="nonexistent", order=1)
        doc = _minimal_doc()
        doc = _replace(doc, elements=(doc.elements[0], e2))
        result = validate(doc)
        assert not result.valid
        assert any(i.code == "dangling_parent" for i in result.issues)

    def test_dangling_caption_ref_rejected(self):
        # table asset references a caption element id that does not exist
        table = TableAsset(
            table_id="t1",
            caption_element_id="ghost-caption",
            page_span_ids=("c1",),
            rows=1,
            columns=1,
            cells=(TableCell(row_index=0, column_index=0, text="x"),),
            header_regions=(),
            footnote_element_ids=(),
        )
        doc = _minimal_doc()
        doc = _replace(doc, structured_assets={"t1": table})
        result = validate(doc)
        assert not result.valid
        assert any(i.code == "dangling_caption_ref" for i in result.issues)

    def test_dangling_cell_source_span_rejected(self):
        table = TableAsset(
            table_id="t1",
            page_span_ids=("c1",),
            rows=1,
            columns=1,
            cells=(TableCell(row_index=0, column_index=0, text="x", source_span_id="ghost-span"),),
            header_regions=(),
            footnote_element_ids=(),
        )
        doc = _minimal_doc()
        doc = _replace(doc, structured_assets={"t1": table})
        result = validate(doc)
        assert not result.valid
        assert any(i.code == "dangling_cell_span" for i in result.issues)

    def test_evidence_span_page_id_must_exist(self):
        span = EvidenceSpan(span_id="s1", page_id="ghost-page")
        e1 = _element("e1")
        e1 = _replace(e1, source_spans=(span,))
        doc = _minimal_doc()
        doc = _replace_elements(doc, (e1, doc.elements[1]))
        result = validate(doc)
        assert not result.valid
        assert any(i.code == "dangling_span_page" for i in result.issues)


# ---------------------------------------------------------------------------
# 3. Enum membership
# ---------------------------------------------------------------------------

class TestEnums:
    def test_invalid_element_type_rejected(self):
        e1 = _element("e1", "not_a_real_type")
        doc = _minimal_doc()
        doc = _replace_elements(doc, (e1, doc.elements[1]))
        result = validate(doc)
        assert not result.valid
        assert any(i.code == "invalid_element_type" for i in result.issues)

    def test_invalid_relation_type_rejected(self):
        bad_rel = Relation(
            source_element_id="e1",
            target_element_id="e2",
            relation_type="invented_relation",
            confidence=1.0,
            method="rule",
        )
        doc = _minimal_doc()
        doc = _replace(doc, relations=(bad_rel,))
        result = validate(doc)
        assert not result.valid
        assert any(i.code == "invalid_relation_type" for i in result.issues)

    def test_invalid_container_type_rejected(self):
        bad_container = _replace(_container(), container_type="martian_page")
        doc = _minimal_doc()
        doc = _replace(doc, containers=(bad_container,))
        result = validate(doc)
        assert not result.valid
        assert any(i.code == "invalid_container_type" for i in result.issues)

    def test_expected_enum_members_present(self):
        # spot-check coverage against SRS §5.3 / §7.3 / §7.5
        assert "paragraph" in VALID_ELEMENT_TYPES
        assert "list_item" in VALID_ELEMENT_TYPES
        assert "selection_mark" in VALID_ELEMENT_TYPES
        assert "parent_of" in VALID_RELATION_TYPES
        assert "continues_on" in VALID_RELATION_TYPES
        assert "page" in VALID_CONTAINER_TYPES
        assert "workbook" in VALID_CONTAINER_TYPES
        assert "source" in VALID_ARTIFACT_CLASSES
        assert "parse_ir" in VALID_ARTIFACT_CLASSES
        # confidence dimensions per SRS §5.3 (multi-dim, not single float)
        for dim in ("text", "layout", "type", "reading_order"):
            assert dim in VALID_CONFIDENCE_DIMENSIONS


# ---------------------------------------------------------------------------
# 4. stable_element_id determinism
# ---------------------------------------------------------------------------

class TestStableElementId:
    def test_same_input_same_output(self):
        a = stable_element_id(scope="doc1", order_index=3)
        b = stable_element_id(scope="doc1", order_index=3)
        assert a == b

    def test_different_scope_different_output(self):
        a = stable_element_id(scope="doc1", order_index=3)
        b = stable_element_id(scope="doc2", order_index=3)
        assert a != b

    def test_different_order_different_output(self):
        a = stable_element_id(scope="doc1", order_index=3)
        b = stable_element_id(scope="doc1", order_index=4)
        assert a != b

    def test_id_is_string(self):
        assert isinstance(stable_element_id(scope="x", order_index=0), str)


# ---------------------------------------------------------------------------
# 5. Table asset fixture (merged cell + header region + caption)
# ---------------------------------------------------------------------------

class TestTableAssetFixture:
    def test_table_asset_validates(self):
        caption = _element("cap1", "caption", order=0)
        table_elem = _replace(
            _element("t1", "table", order=1),
            source_spans=(_span("cell-span-1"),),
        )
        table = TableAsset(
            table_id="t1",
            caption_element_id="cap1",
            page_span_ids=("c1",),
            rows=2,
            columns=2,
            cells=(
                TableCell(row_index=0, column_index=0, text="A", is_header=True, source_span_id="cell-span-1"),
                TableCell(row_index=0, column_index=1, text="B", is_header=True, source_span_id="cell-span-1"),
                TableCell(row_index=1, column_index=0, column_span=2, text="merged", is_header=False, source_span_id="cell-span-1"),
            ),
            header_regions=((0, 0),),
            footnote_element_ids=(),
        )
        rel = Relation(
            source_element_id="cap1",
            target_element_id="t1",
            relation_type="caption_of",
            confidence=0.9,
            method="layout_rule",
        )
        doc = _replace(
            _minimal_doc(),
            elements=(caption, table_elem),
            relations=(rel,),
            structured_assets={"t1": table},
        )
        result = validate(doc)
        assert result.valid, f"unexpected issues: {result.issues}"


# ---------------------------------------------------------------------------
# 6. Frozen immutability
# ---------------------------------------------------------------------------

class TestImmutability:
    @pytest.mark.parametrize(
        "obj,attr,value",
        [
            (Confidence(text=1.0, source="x"), "text", 0.5),
            (EvidenceSpan(span_id="s1"), "span_id", "s2"),
            (Relation("a", "b", "parent_of", 1.0, "m"), "confidence", 0.1),
            (_container(), "container_type", "page"),
            (_element("e1"), "text", "changed"),
        ],
    )
    def test_frozen_assignment_raises(self, obj, attr, value):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, attr, value)


# ---------------------------------------------------------------------------
# 7. JSON round-trip
# ---------------------------------------------------------------------------

class TestJsonRoundTrip:
    @pytest.mark.parametrize("fixture_name", ["simple_markdown_ir.json", "table_ir.json"])
    def test_fixture_loads_and_validates(self, fixture_name):
        raw = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
        doc = ParsedDocument.from_dict(raw)
        result = validate(doc)
        assert result.valid, f"{fixture_name}: {result.issues}"

    def test_roundtrip_preserves_structure(self):
        raw = json.loads((FIXTURES / "simple_markdown_ir.json").read_text(encoding="utf-8"))
        doc = ParsedDocument.from_dict(raw)
        dumped = doc.to_dict()
        reloaded = ParsedDocument.from_dict(dumped)
        assert dumped == reloaded.to_dict()


# ---------------------------------------------------------------------------
# 7b. WP0.5 fixture coverage (pdf_digital, docx, invalid_dangling)
# ---------------------------------------------------------------------------
class TestFixtures:
    """Round-trip + validate coverage for the WP0.5 fixtures.

    See ``fixtures/README.md`` for the SRS scenario each fixture maps to.
    """

    @pytest.mark.parametrize(
        "fixture_name",
        ["simple_markdown_ir.json", "table_ir.json", "pdf_digital_ir.json", "docx_ir.json"],
    )
    def test_well_formed_fixture_validates(self, fixture_name):
        raw = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
        doc = ParsedDocument.from_dict(raw)
        result = validate(doc)
        assert result.valid, f"{fixture_name}: {result.issues}"

    def test_pdf_digital_carries_expected_shape(self):
        """SRS §A02: pages, heading tree, paragraph bbox, table, figure."""
        raw = json.loads((FIXTURES / "pdf_digital_ir.json").read_text(encoding="utf-8"))
        doc = ParsedDocument.from_dict(raw)
        # 3 page containers.
        assert len(doc.containers) == 3
        assert all(c.container_type == "page" for c in doc.containers)
        # Multi-level heading tree (title -> heading L1 -> heading L2).
        headings = [e for e in doc.elements if e.element_type == "heading"]
        levels = {h.style.get("level") for h in headings}
        assert {1, 2}.issubset(levels)
        # Paragraph carries a visual_region bbox.
        para = next(e for e in doc.elements if e.element_type == "paragraph")
        assert para.source_spans[0].visual_region is not None
        assert para.source_spans[0].visual_region["kind"] == "bbox"
        # One table asset + one figure asset.
        kinds = {a["kind"] for a in doc.to_dict()["structured_assets"].values()}
        assert {"table", "figure"} == kinds
        # caption_of + contains + next_in_reading_order relations present.
        rel_types = {r.relation_type for r in doc.relations}
        assert {"caption_of", "contains", "next_in_reading_order", "parent_of"}.issubset(rel_types)

    def test_docx_carries_expected_shape(self):
        """SRS §A04 (merged cells + table footnote) + DOCX nested lists."""
        raw = json.loads((FIXTURES / "docx_ir.json").read_text(encoding="utf-8"))
        doc = ParsedDocument.from_dict(raw)
        # Nested list / list_item (two levels).
        lists = [e for e in doc.elements if e.element_type == "list"]
        list_levels = {l.style.get("list_level") for l in lists}
        assert {0, 1}.issubset(list_levels)
        # Merged cell with row_span.
        table = doc.structured_assets["tbl-asset-00001"]
        assert isinstance(table, TableAsset)
        merged = [c for c in table.cells if c.row_span > 1]
        assert len(merged) == 1
        assert merged[0].row_span == 2
        # Header region spans 2 rows.
        assert table.header_regions == ((0, 1),)
        # Footnote element + footnote_of relation.
        footnotes = [e for e in doc.elements if e.element_type == "footnote"]
        assert len(footnotes) == 1
        assert "footnote_of" in {r.relation_type for r in doc.relations}
        # Table references the footnote element.
        assert footnotes[0].element_id in table.footnote_element_ids

    def test_invalid_dangling_fixture_rejected(self):
        """SRS §4.7: dangling relation + illegal element_type => normalization failure."""
        raw = json.loads(
            (FIXTURES / "invalid_dangling_ir.json").read_text(encoding="utf-8")
        )
        doc = ParsedDocument.from_dict(raw)
        result = validate(doc)
        assert not result.valid, "dangling fixture must NOT validate"
        codes = {i.code for i in result.issues if i.level == "error"}
        # Illegal element_type caught by jsonschema enum check.
        assert "invalid_element_type" in codes
        # Dangling relation target caught by referential-integrity check.
        assert "dangling_relation" in codes

    def test_invalid_fixture_round_trip_is_stable(self):
        """Even invalid IR must load + re-serialize without data loss."""
        raw = json.loads(
            (FIXTURES / "invalid_dangling_ir.json").read_text(encoding="utf-8")
        )
        doc = ParsedDocument.from_dict(raw)
        dumped = doc.to_dict()
        reloaded = ParsedDocument.from_dict(dumped)
        assert dumped == reloaded.to_dict()


# ---------------------------------------------------------------------------
# 8. Validation result shape
# ---------------------------------------------------------------------------

class TestValidationResult:
    def test_valid_result_has_no_errors(self):
        r = validate(_minimal_doc())
        assert isinstance(r, ValidationResult)
        assert r.valid is True
        assert all(i.level != "error" for i in r.issues)

    def test_validation_issue_levels(self):
        # ValidationResult / ValidationIssue are proper frozen dataclasses
        assert is_dataclass(ValidationResult)
        assert ValidationResult.__dataclass_params__.frozen is True
        assert is_dataclass(ValidationIssue)
        assert ValidationIssue.__dataclass_params__.frozen is True


# ---------------------------------------------------------------------------
# 9. Confidence multi-dimension (SRS §5.3 — not a single float)
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_all_dimensions_optional(self):
        c = Confidence(source="native")
        assert c.text is None
        assert c.layout is None
        assert c.source == "native"

    def test_partial_dimensions(self):
        c = Confidence(text=0.9, source="docling")
        assert c.text == 0.9
        assert c.type is None

    def test_out_of_range_rejected(self):
        doc = _minimal_doc()
        e1 = _replace(doc.elements[0], confidence=Confidence(text=1.5, source="x"))
        doc = _replace_elements(doc, (e1, doc.elements[1]))
        result = validate(doc)
        assert not result.valid
        assert any(i.code == "confidence_out_of_range" for i in result.issues)


# ---------------------------------------------------------------------------
# 10. Unknown span types not fabricated (SRS §7.4)
# ---------------------------------------------------------------------------

class TestEvidenceSpanTypes:
    def test_evidence_span_supports_four_locator_kinds(self):
        # text_range
        s1 = EvidenceSpan(span_id="s1", text_range=(0, 3))
        # source_locator
        s2 = EvidenceSpan(span_id="s2", source_locator={"kind": "ooxml", "ref": "p1"})
        # visual_region
        s3 = EvidenceSpan(span_id="s3", visual_region={"kind": "bbox", "x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0})
        # native_ref
        s4 = EvidenceSpan(span_id="s4", native_ref={"kind": "sheet_cell", "sheet": "S1", "cell": "A1"})
        for s in (s1, s2, s3, s4):
            assert s.span_id.startswith("s")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _replace(doc: ParsedDocument, **changes) -> ParsedDocument:
    """Return a new ParsedDocument with field replacements (immutable update)."""
    from dataclasses import replace as _dc_replace
    return _dc_replace(doc, **changes)


def _replace_elements(doc: ParsedDocument, elements: tuple) -> ParsedDocument:
    return _replace(doc, elements=tuple(elements))
