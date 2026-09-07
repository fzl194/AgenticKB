"""A1 来源记录纯投影（extract）——每格式位置通道判定（38 号 §5 测试矩阵）.

覆盖：PDF 页（含跨页取首页）/ MD·TXT 行号并集 / XLSX sheet+cell /
DOCX 段索引与表格描述 / HTML xpath / PPTX slide / 三通道全空 unavailable /
section·document 恒 section_only / alias 不产行 / 章节锚父链 / 格式口径。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parse_ir.enums import (
    PARSE_IR_SCHEMA_VERSION,
)
from knowledge_mining.mining.contracts.parse_ir.types import (
    Container,
    Element,
    EvidenceSpan,
    ParseIdentity,
    ParsedDocument,
)
from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrievalRepresentation,
)
from knowledge_mining.mining.source_locator.extract import (
    extract_locator_records,
    source_format_of,
)


def _doc(
    *,
    elements=(),
    containers=(),
    parser_fp: str = "native_pdf@1",
) -> ParsedDocument:
    return ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=ParseIdentity(
            source_raw_hash="raw-1",
            parser_fingerprint=parser_fp,
            parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
        ),
        containers=tuple(containers),
        elements=tuple(elements),
    )


def _rep(
    rep_id: str,
    *,
    rep_type: str = "segment",
    target_type: str = "segment",
    target_ref: str | None = None,
    facets: dict | None = None,
    source_refs: tuple[dict, ...] = (),
    container_ref: str | None = None,
) -> RetrievalRepresentation:
    return RetrievalRepresentation(
        representation_id=rep_id,
        representation_type=rep_type,
        content_type=rep_type,
        content_text="text",
        target_type=target_type,
        target_ref=target_ref or f"doc-x#seg:{rep_id}",
        canonical_evidence_id=rep_id,
        source_refs=source_refs,
        container_ref=container_ref,
        facets=facets if facets is not None else {"document": "doc-x"},
    )


def _span(span_id: str, **kwargs) -> EvidenceSpan:
    return EvidenceSpan(span_id=span_id, **kwargs)


def _element(
    eid: str,
    *,
    etype: str = "paragraph",
    spans=(),
    parent: str | None = None,
) -> Element:
    return Element(
        element_id=eid,
        element_type=etype,
        order_index=0,
        parent_id=parent,
        source_spans=tuple(spans),
    )


# ---------------------------------------------------------------------------
# PDF：页容器优先，跨页取首页；cell native_ref 保留
# ---------------------------------------------------------------------------


def test_pdf_page_from_container_page_number() -> None:
    containers = (
        Container(container_id="c-page-0000", container_type="page",
                  order_index=0, page_number=1),
        Container(container_id="c-page-0001", container_type="page",
                  order_index=1, page_number=2),
    )
    elements = (
        _element("p1", spans=(_span("s1", page_id="c-page-0001"),), parent="h1"),
        _element("h1", etype="heading", spans=()),
    )
    records = extract_locator_records(
        _doc(elements=elements, containers=containers),
        (_rep("r1", source_refs=(
            {"element_id": "p1", "evidence_span_ids": ("s1",)},
        ),),),
    )
    assert len(records) == 1
    record = records[0]
    assert record.locator_kind == "page"
    assert record.page == 2
    assert record.source_format == "pdf"
    assert record.section_element_id == "h1"


def test_pdf_cross_page_takes_first_page() -> None:
    containers = tuple(
        Container(container_id=f"c-page-{i:04d}", container_type="page",
                  order_index=i, page_number=i + 1)
        for i in range(3)
    )
    elements = (
        _element("p1", spans=(
            _span("s1", page_id="c-page-0002"),
            _span("s2", page_id="c-page-0000"),
        )),
    )
    records = extract_locator_records(
        _doc(elements=elements, containers=containers),
        (_rep("r1", source_refs=(
            {"element_id": "p1", "evidence_span_ids": ("s1", "s2")},
        )),),
    )
    assert records[0].locator_kind == "page"
    assert records[0].page == 1  # 首页


def test_pdf_table_row_cell_native_ref_kept() -> None:
    elements = (
        _element("t1", spans=(
            _span("cell-1", native_ref={"page": 2, "cell": [1, 0]}),
        )),
    )
    records = extract_locator_records(
        _doc(elements=elements),
        (_rep("r1", rep_type="table_row",
              target_ref="doc-x#table_row:t1-table:3",
              container_ref="t1-table",
              source_refs=(
                  {"element_id": "t1", "evidence_span_ids": ("cell-1",)},
              )),),
    )
    record = records[0]
    assert record.locator_kind == "page"
    assert record.page == 3  # native_ref page=2 → 1 基
    assert record.table_ref == "t1-table"
    assert record.row_index == 3
    assert record.native_ref == {"page": 2, "cell": [1, 0]}


# ---------------------------------------------------------------------------
# MD/TXT：行号并集；CJK 文本不影响行号口径
# ---------------------------------------------------------------------------


def test_markdown_line_range_union() -> None:
    elements = (
        _element("p1", spans=(
            _span("s1", source_locator={"line_start": 12, "line_end": 20},
                  raw_text="中文段落"),
            _span("s2", source_locator={"line_start": 5, "line_end": 9},
                  raw_text="🎉emoji 段"),
        )),
    )
    records = extract_locator_records(
        _doc(elements=elements, parser_fp="legacy_markdown@1"),
        (_rep("r1", source_refs=(
            {"element_id": "p1", "evidence_span_ids": ("s1", "s2")},
        )),),
    )
    record = records[0]
    assert record.locator_kind == "line_range"
    assert (record.line_start, record.line_end) == (5, 20)
    assert record.source_format == "md"


def test_line_fields_absent_when_kind_is_not_line_range() -> None:
    """PDF 上不落行号（pdf_normalizer 契约：无行语义不伪造）."""
    containers = (Container(container_id="c0", container_type="page",
                            order_index=0, page_number=1),)
    elements = (
        _element("p1", spans=(
            _span("s1", page_id="c0",
                  source_locator={"line_start": 3, "line_end": 4}),
        )),
    )
    records = extract_locator_records(
        _doc(elements=elements, containers=containers),
        (_rep("r1", source_refs=(
            {"element_id": "p1", "evidence_span_ids": ("s1",)},
        )),),
    )
    assert records[0].locator_kind == "page"
    assert records[0].line_start is None
    assert records[0].line_end is None


# ---------------------------------------------------------------------------
# XLSX：sheet 名 + 绝对 A1
# ---------------------------------------------------------------------------


def test_xlsx_sheet_cell() -> None:
    containers = (
        Container(container_id="wb", container_type="workbook", order_index=0),
        Container(container_id="sh-1", container_type="sheet", order_index=0,
                  name="告警表", parent_container_id="wb"),
    )
    elements = (
        _element("t1", spans=(
            _span("cell-1", native_ref={"sheet": "告警表", "cell": "B7"}),
        )),
    )
    records = extract_locator_records(
        _doc(elements=elements, containers=containers, parser_fp="native_xlsx@1"),
        (_rep("r1", rep_type="table_row",
              target_ref="doc-x#table_row:t1-table:7",
              container_ref="t1-table",
              source_refs=(
                  {"element_id": "t1", "evidence_span_ids": ("cell-1",)},
              )),),
    )
    record = records[0]
    assert record.locator_kind == "sheet_cell"
    assert record.sheet == "告警表"
    assert record.cell == "B7"
    assert record.row_index == 7
    assert record.source_format == "xlsx"


# ---------------------------------------------------------------------------
# DOCX / HTML / PPTX：native 级 + 人读 description
# ---------------------------------------------------------------------------


def test_docx_paragraph_native_description() -> None:
    elements = (
        _element("p1", spans=(
            _span("s1", native_ref={"paragraph_index": 11}),
        )),
    )
    records = extract_locator_records(
        _doc(elements=elements, parser_fp="native_docx@1"),
        (_rep("r1", source_refs=(
            {"element_id": "p1", "evidence_span_ids": ("s1",)},
        )),),
    )
    record = records[0]
    assert record.locator_kind == "native"
    assert record.description == "第 12 段"
    assert record.source_format == "docx"


def test_docx_table_row_description() -> None:
    elements = (
        _element("t1", spans=(
            _span("c1", native_ref={
                "table_index": 2, "row_index": 3, "column_index": 1,
            }),
        )),
    )
    records = extract_locator_records(
        _doc(elements=elements, parser_fp="native_docx@1"),
        (_rep("r1", rep_type="table_row",
              target_ref="doc-x#table_row:t1:3", container_ref="t1",
              source_refs=(
                  {"element_id": "t1", "evidence_span_ids": ("c1",)},
              )),),
    )
    record = records[0]
    assert record.locator_kind == "native"
    assert record.description == "表格 3 第 4 行 · 第 2 列"


def test_html_xpath_native_without_description() -> None:
    elements = (
        _element("p1", spans=(
            _span("s1", native_ref={"xpath": "/html/body/div[3]/p[2]"}),
        )),
    )
    records = extract_locator_records(
        _doc(elements=elements, parser_fp="native_html@1"),
        (_rep("r1", source_refs=(
            {"element_id": "p1", "evidence_span_ids": ("s1",)},
        )),),
    )
    record = records[0]
    assert record.locator_kind == "native"
    assert record.native_ref == {"xpath": "/html/body/div[3]/p[2]"}
    assert record.description is None


def test_pptx_slide_description() -> None:
    elements = (
        _element("p1", spans=(
            _span("s1", native_ref={"slide_index": 4, "shape_index": 0}),
        )),
    )
    records = extract_locator_records(
        _doc(elements=elements, parser_fp="native_pptx@1"),
        (_rep("r1", source_refs=(
            {"element_id": "p1", "evidence_span_ids": ("s1",)},
        )),),
    )
    record = records[0]
    assert record.locator_kind == "native"
    assert record.description == "幻灯片 5"


# ---------------------------------------------------------------------------
# 语义分层：unavailable / section_only / alias 跳过
# ---------------------------------------------------------------------------


def test_unavailable_when_no_locator_channel() -> None:
    elements = (
        _element("p1", spans=(_span("s1", raw_text="无定位 span"),)),
    )
    records = extract_locator_records(
        _doc(elements=elements),
        (_rep("r1", source_refs=(
            {"element_id": "p1", "evidence_span_ids": ("s1",)},
        )),),
    )
    assert records[0].locator_kind == "unavailable"
    assert records[0].page is None


def test_section_and_document_are_section_only() -> None:
    records = extract_locator_records(
        _doc(),
        (
            _rep("d1", rep_type="document", target_type="document",
                 target_ref="doc-x#document"),
            _rep("s1", rep_type="section", target_type="section",
                 target_ref="doc-x#section:第一章/1.1",
                 facets={"document": "doc-x", "section_path": "第一章 > 1.1"}),
        ),
    )
    assert {r.locator_kind for r in records} == {"section_only"}
    section = next(r for r in records if r.representation_id == "s1")
    assert section.section_path == "第一章 > 1.1"


def test_alias_representations_are_skipped() -> None:
    records = extract_locator_records(
        _doc(),
        (
            _rep("a1", rep_type="query_alias"),
            _rep("a2", rep_type="summary_alias"),
        ),
    )
    assert records == ()


def test_table_representation_gets_table_ref_without_row() -> None:
    records = extract_locator_records(
        _doc(),
        (_rep("t1", rep_type="table", target_type="table",
              target_ref="doc-x#table:t1-table", container_ref="t1-table"),),
    )
    assert records[0].table_ref == "t1-table"
    assert records[0].row_index is None


def test_section_anchor_walks_parent_chain() -> None:
    elements = (
        _element("h0", etype="heading"),
        _element("h1", etype="heading", parent="h0"),
        _element("p1", parent="h1"),
    )
    records = extract_locator_records(
        _doc(elements=elements),
        (_rep("r1", source_refs=(
            {"element_id": "p1", "evidence_span_ids": ()},
        )),),
    )
    assert records[0].section_element_id == "h1"


def test_source_format_mapping() -> None:
    assert source_format_of("native_pdf@1#pdfplumber") == "pdf"
    assert source_format_of("pdf_text_layer@2#pdfminer") == "pdf"
    assert source_format_of("legacy_txt@1") == "txt"
    assert source_format_of("rendered_text@1") == "txt"
    assert source_format_of("unknown_parser@1") == "other"
    assert source_format_of(None) == "other"
