"""Legacy line-oriented Normalizer（M2 WP4, SRS §C07 / §4.7）.

要点：
- block_type -> element_type 映射：markdown 词表对齐 VALID_ELEMENT_TYPES；
  无法映射的落 ``unknown`` 并记 warning，不伪造类型（SRS §7.4）。
- 单一 ``section`` 文档级容器（"c-doc"）：MD/TXT 无页概念，page_number
  留 None 不伪造（SRS §3.6）。
- 每元素：``stable_element_id``（scope=source_raw_hash，纯位置 id，同输入
  同 id，SRS §2.1 / §4.7）；EvidenceSpan 带 line_start/line_end 的
  source_locator + text_range + 原文行 raw_text（SRS §A01 行可回溯）。
- markdown heading 用 level 建 parent 链（父标题 = 最近的上一个更浅
  level 的 heading），产出 parent_id + parent_of relation；相邻元素产出
  next_in_reading_order relation。
- markdown table block -> element(table) + TableAsset（cells 保留 raw
  text，首行为表头 is_header；无页容器时 page_span_ids 为空 tuple，
  validator 不要求非空）。
- normalized_text 仅做最小规范化（strip），不改写含义。
- 产出后调用 ``parse_ir.validate``；error-level issue -> raise
  ValueError（normalization failure 不得进入质量门禁，SRS §4.7）。
"""
from __future__ import annotations

from collections.abc import Mapping

from knowledge_mining.mining.contracts.parse_ir import (
    PARSE_IR_SCHEMA_VERSION,
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
from knowledge_mining.mining.parse_adapters.legacy_markdown import (
    LEGACY_MARKDOWN_FINGERPRINT,
    LEGACY_MARKDOWN_PARSER_ID,
)
from knowledge_mining.mining.parse_adapters.legacy_txt import (
    LEGACY_TXT_FINGERPRINT,
    LEGACY_TXT_PARSER_ID,
)

NORMALIZER_VERSION = "legacy-line@1"

# 单一文档级 section 容器 id（MD/TXT 无页容器，SRS §3.6）。
DOC_CONTAINER_ID = "c-doc"

# backend block_type -> Parse IR element_type（SRS §4.7 映射表）。
_BLOCK_TYPE_TO_ELEMENT_TYPE: dict[str, str] = {
    "heading": "heading",
    "paragraph": "paragraph",
    "list": "list",
    "list_item": "list_item",
    "code": "code",
    "quote": "quote",
    "table": "table",
    "html_table": "table",
    # MD 图片在压缩版 WP4 未接 binary asset 通道，不伪造 figure。
    "image": "unknown",
    "raw_html": "unknown",
}

_DEFAULT_FINGERPRINTS: dict[str, str] = {
    LEGACY_MARKDOWN_PARSER_ID: LEGACY_MARKDOWN_FINGERPRINT,
    LEGACY_TXT_PARSER_ID: LEGACY_TXT_FINGERPRINT,
}


class LegacyLineNormalizer:
    """ParseIRNormalizer 实现：行导向 backend artifact -> Parse IR（§C07）."""

    def __init__(
        self, *, parser_fingerprints: Mapping[str, str] | None = None
    ) -> None:
        merged = dict(_DEFAULT_FINGERPRINTS)
        merged.update(parser_fingerprints or {})
        self._fingerprints = merged

    def normalize(
        self,
        artifact: BackendParseArtifact,
        *,
        source_raw_hash: str,
        parse_run_id: str | None = None,
    ) -> ParsedDocument:
        source_lines = artifact.raw_output.split("\n") if artifact.raw_output else []
        warnings = list(artifact.warnings)
        elements, relations, assets = _build_element_graph(
            artifact.blocks, source_raw_hash, source_lines, warnings
        )

        doc = ParsedDocument(
            schema_version=PARSE_IR_SCHEMA_VERSION,
            source_identity=ParseIdentity(
                source_raw_hash=source_raw_hash,
                parser_fingerprint=self._fingerprint_for(artifact),
                normalizer_version=NORMALIZER_VERSION,
            ),
            containers=(Container(
                container_id=DOC_CONTAINER_ID,
                container_type="section",
                order_index=0,
                name="document",
            ),),
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

    def _fingerprint_for(self, artifact: BackendParseArtifact) -> str:
        return self._fingerprints.get(
            artifact.parser_id,
            f"{artifact.parser_id}@{artifact.parser_version}",
        )


# ---------------------------------------------------------------------------
# 模块级纯函数（便于单测）


def _build_element_graph(
    blocks: tuple[BackendBlock, ...],
    source_raw_hash: str,
    source_lines: list[str],
    warnings: list[str],
) -> tuple[list[Element], list[Relation], dict[str, TableAsset]]:
    """把 backend block 序列编译为 element 图（元素 + 关系 + 表格资产）.

    逐块：类型映射（未映射落 ``unknown`` 并记 warning，§7.4）、stable id、
    heading 弹栈 parent 链、阅读序链接、行级 EvidenceSpan、table 资产。
    """
    elements: list[Element] = []
    relations: list[Relation] = []
    assets: dict[str, TableAsset] = {}
    heading_stack: list[tuple[int, str]] = []
    prev_id: str | None = None

    for order, block in enumerate(blocks):
        element_type = _BLOCK_TYPE_TO_ELEMENT_TYPE.get(block.block_type)
        if element_type is None:
            element_type = "unknown"
            warnings.append(
                f"unmapped block_type {block.block_type!r} -> 'unknown'"
            )
        element_id = stable_element_id(source_raw_hash, order)
        parent_id = _resolve_parent(heading_stack, block, element_type, element_id)

        elements.append(Element(
            element_id=element_id,
            element_type=element_type,
            order_index=order,
            text=block.text,
            normalized_text=block.text.strip(),
            parent_id=parent_id,
            source_spans=(_make_span(element_id, block, source_lines),),
            style=_make_style(block),
        ))
        relations.extend(_element_relations(prev_id, parent_id, element_id))
        if element_type == "table":
            asset = _table_asset(element_id, block)
            if asset is not None:
                assets[asset.table_id] = asset
        prev_id = element_id

    return elements, relations, assets
# ---------------------------------------------------------------------------

def _resolve_parent(
    heading_stack: list[tuple[int, str]],
    block: BackendBlock,
    element_type: str,
    element_id: str,
) -> str | None:
    """heading 弹栈建链；非 heading 挂到最近的标题下（无则 None）."""
    if element_type == "heading":
        level = block.level if block.level and block.level > 0 else 1
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        parent = heading_stack[-1][1] if heading_stack else None
        heading_stack.append((level, element_id))
        return parent
    return heading_stack[-1][1] if heading_stack else None


def _make_span(
    element_id: str, block: BackendBlock, source_lines: list[str]
) -> EvidenceSpan:
    """行可回溯 EvidenceSpan：source_locator 行区间 + 原文行 raw_text."""
    locator: dict[str, int] | None = None
    raw_text: str | None = None
    if block.line_start is not None and block.line_end is not None:
        locator = {"line_start": block.line_start, "line_end": block.line_end}
        end = max(block.line_start + 1, min(block.line_end, len(source_lines)))
        if 0 <= block.line_start < len(source_lines):
            raw_text = "\n".join(source_lines[block.line_start:end])
    return EvidenceSpan(
        span_id=f"{element_id}-s0",
        source_locator=locator,
        text_range=(0, len(block.text)),
        raw_text=raw_text if raw_text is not None else (block.text or None),
    )


def _make_style(block: BackendBlock) -> dict[str, object]:
    style: dict[str, object] = {}
    if block.level is not None:
        style["level"] = block.level
    language = (block.structure or {}).get("language")
    if language:
        style["language"] = language
    return style


def _element_relations(
    prev_id: str | None, parent_id: str | None, element_id: str
) -> list[Relation]:
    out: list[Relation] = []
    if parent_id is not None:
        out.append(Relation(
            source_element_id=parent_id,
            target_element_id=element_id,
            relation_type="parent_of",
            method=NORMALIZER_VERSION,
        ))
    if prev_id is not None:
        out.append(Relation(
            source_element_id=prev_id,
            target_element_id=element_id,
            relation_type="next_in_reading_order",
            method=NORMALIZER_VERSION,
        ))
    return out


def _table_asset(element_id: str, block: BackendBlock) -> TableAsset | None:
    """markdown/html table structure -> TableAsset（cells 保留 raw text）."""
    structure = block.structure or {}
    explicit_columns = [str(c) for c in (structure.get("columns") or [])]
    rows = [dict(r) for r in (structure.get("rows") or [])]
    columns = explicit_columns or (list(rows[0].keys()) if rows else [])
    if not columns:
        return None

    has_header = bool(explicit_columns)
    grid: list[list[str]] = ([explicit_columns] if has_header else [])
    grid.extend([str(row.get(col, "")) for col in columns] for row in rows)

    cells = tuple(
        TableCell(
            row_index=r,
            column_index=c,
            text=grid[r][c],
            is_header=has_header and r == 0,
        )
        for r in range(len(grid))
        for c in range(len(columns))
    )
    return TableAsset(
        table_id=f"{element_id}-table",
        page_span_ids=(),  # 无页容器；validator 不要求非空
        rows=len(grid),
        columns=len(columns),
        cells=cells,
        header_regions=((0, 0),) if has_header else (),
    )


__all__ = [
    "DOC_CONTAINER_ID",
    "NORMALIZER_VERSION",
    "LegacyLineNormalizer",
]
