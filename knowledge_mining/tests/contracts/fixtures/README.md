# Parse IR Fixtures

Canonical Parse IR v0.1 JSON documents used by the contract test suite and
later by WP3 (validation) / WP8 (regression). Each fixture maps to one or more
SRS acceptance scenarios in
`docs/文档解析平台化-能力规格与工作拆解.md`.

| Fixture | Shape | SRS scenario | Notes |
|---|---|---|---|
| `simple_markdown_ir.json` | 1 `dom_document` container, title + heading + paragraph, line-addressable spans, `parent_of` + `next_in_reading_order` | §A01 简单 Markdown | Markdown round-trip; segments can trace back to line numbers. |
| `table_ir.json` | 1 `page`, table element + caption, `caption_of`, 3×3 table with a `column_span=2` merged cell + `header_regions` | §A04 复杂表格 (merged cells, header regions) | Merged-cell + header-region acceptance. |
| `pdf_digital_ir.json` | 3 `page` containers, multi-level heading tree, paragraph with `visual_region` bbox, table (header region + plain cells), figure asset + caption, `caption_of` / `contains` / `next_in_reading_order` (incl. cross-page) / `parent_of`; per-element multi-dim Confidence | §A02 数字 PDF | Digital PDF: pages, heading tree, paragraph bbox, tables; retrieval results can locate page + region. |
| `docx_ir.json` | 1 `section` container, nested `list` / `list_item` (2 levels), merged-cell table (`row_span=2`, `header_regions` spanning 2 rows), `footnote` element + `footnote_of` relation, OOXML `source_locator` provenance | §A04 复杂表格 (row/col spans, table footnotes) + DOCX structure | DOCX-style nested lists, merged-cell table with table-level footnote. |
| `invalid_dangling_ir.json` | Intentionally broken: `element_type="totally_made_up_type"` (illegal enum), a relation whose `target_element_id` does not exist (`ghost-element-999`) | §4.7 normalization failure — "后端返回成功但 JSON 缺字段、关系悬空或坐标非法时，记为 normalization failure，不可进入质量门禁" | Loaded by tests to assert `validate()` returns `valid=False` with codes `invalid_element_type` and `dangling_relation`. |

## Round-trip expectation

All fixtures MUST round-trip through `ParsedDocument.from_dict` → `validate`.
The four well-formed fixtures (`simple_markdown_ir`, `table_ir`,
`pdf_digital_ir`, `docx_ir`) MUST validate to `valid=True`.
`invalid_dangling_ir.json` MUST validate to `valid=False` and surface the
error codes listed above.

See `knowledge_mining/tests/contracts/test_parse_ir.py::TestFixtures` for the
executable contract.
