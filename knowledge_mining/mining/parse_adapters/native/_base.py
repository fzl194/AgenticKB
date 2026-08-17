"""M3 原生格式 Normalizer 公共骨架（SRS §C07 / §4.7, ADR-0003 D-028A）.

四个原生适配器（DOCX/XLSX/PPTX/HTML）的 Normalizer 共性：
- backend block -> element 类型映射（未映射落 ``unknown`` + warning，§7.4）；
- heading 弹栈 parent 链（父标题 = 最近的上一个更浅 level 的 heading）；
- 逐元素 next_in_reading_order；
- ``stable_element_id(scope=source_raw_hash, order_index)`` 纯位置 id；
- structure 驱动的 TableAsset 构建（cells 网格 + span + 首行 is_header 约定，
  cell 可用 ``evidence_index`` 关联到元素级 EvidenceSpan）；
- 产出后强制 ``parse_ir.validate``，error-level issue -> raise ValueError
  （normalization failure 不可进入质量门禁，SRS §4.7）。

差异点（容器构造、span 构造、容器归属）以钩子方法下放到子类：
``_build_containers`` / ``_container_id_for`` / ``_make_spans`` /
``_element_type_for`` / ``_table_asset``。
M2 的 ``LegacyLineNormalizer`` 保持不动（纪律：不修改 M2 既有文件）。
"""
from __future__ import annotations

from collections.abc import Mapping

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


class BaseNativeNormalizer:
    """ParseIRNormalizer 公共骨架：模板方法 + 钩子（SRS §C07）."""

    #: 子类必须覆盖：normalizer 版本标识（进 ParseIdentity）。
    normalizer_version: str = "native-base@0"
    #: 子类必须覆盖：parser_id -> parser_fingerprint 默认表。
    _default_fingerprints: dict[str, str] = {}
    #: 子类可覆盖：backend block_type -> element_type 映射表。
    _element_type_map: dict[str, str] = {}

    def __init__(
        self, *, parser_fingerprints: Mapping[str, str] | None = None
    ) -> None:
        merged = dict(self._default_fingerprints)
        merged.update(parser_fingerprints or {})
        self._fingerprints = merged

    # -- 模板方法 ---------------------------------------------------------

    def normalize(
        self,
        artifact: BackendParseArtifact,
        *,
        source_raw_hash: str,
        parse_run_id: str | None = None,
    ) -> ParsedDocument:
        warnings = list(artifact.warnings)
        containers = self._build_containers(artifact)
        elements, relations, assets = self._build_element_graph(
            artifact.blocks, source_raw_hash, containers, warnings
        )
        doc = ParsedDocument(
            schema_version=PARSE_IR_SCHEMA_VERSION,
            source_identity=ParseIdentity(
                source_raw_hash=source_raw_hash,
                parser_fingerprint=self._fingerprints.get(
                    artifact.parser_id,
                    f"{artifact.parser_id}@{artifact.parser_version}",
                ),
                normalizer_version=self.normalizer_version,
            ),
            containers=containers,
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

    # -- 共享图构建 -------------------------------------------------------

    def _build_element_graph(
        self,
        blocks: tuple[BackendBlock, ...],
        source_raw_hash: str,
        containers: tuple[Container, ...],
        warnings: list[str],
    ) -> tuple[list[Element], list[Relation], dict[str, TableAsset]]:
        """逐块编译 element 图：类型映射 -> stable id -> parent 链 -> span."""
        elements: list[Element] = []
        relations: list[Relation] = []
        assets: dict[str, TableAsset] = {}
        heading_stack: list[tuple[int, str]] = []
        prev_id: str | None = None
        last_container: str | None = None

        for order, block in enumerate(blocks):
            container_id = self._container_id_for(block, containers)
            if (
                self.reset_heading_stack_on_container_change
                and container_id is not None
                and last_container is not None
                and container_id != last_container
            ):
                heading_stack.clear()  # 容器（slide）边界：父链不跨容器
            last_container = container_id
            element_type = self._element_type_for(block, warnings)
            element_id = stable_element_id(source_raw_hash, order)
            parent_id = _resolve_parent(
                heading_stack, block, element_type, element_id
            )
            spans = self._make_spans(element_id, block, container_id)

            elements.append(Element(
                element_id=element_id,
                element_type=element_type,
                order_index=order,
                text=block.text,
                normalized_text=block.text.strip(),
                parent_id=parent_id,
                page_span_ids=(container_id,) if container_id else (),
                source_spans=spans,
                style=_make_style(block),
                parser_annotations=_filtered_annotations(block),
                confidence=Confidence(source="native"),
            ))
            relations.extend(_element_relations(prev_id, parent_id, element_id))
            if element_type == "table":
                asset = self._table_asset(element_id, block, spans, container_id)
                if asset is not None:
                    assets[asset.table_id] = asset
            prev_id = element_id

        return elements, relations, assets

    # -- 钩子（子类实现 / 覆盖） ------------------------------------------

    def _build_containers(
        self, artifact: BackendParseArtifact
    ) -> tuple[Container, ...]:
        raise NotImplementedError

    #: 子类可覆盖：容器切换是否重置 heading 父链（如 PPTX 以 slide 为界，
    #: 避免无标题 slide 的正文误挂到上一张 slide 的标题，评审 MED）。
    reset_heading_stack_on_container_change: bool = False

    def _container_id_for(
        self, block: BackendBlock, containers: tuple[Container, ...]
    ) -> str | None:
        """块所属容器 id（无容器语义时返回 None，不伪造）。"""
        return None

    def _make_spans(
        self,
        element_id: str,
        block: BackendBlock,
        container_id: str | None,
    ) -> tuple[EvidenceSpan, ...]:
        """构造该元素的 EvidenceSpan 序列（子类必须实现）。"""
        raise NotImplementedError

    def _element_type_for(
        self, block: BackendBlock, warnings: list[str]
    ) -> str:
        mapped = self._element_type_map.get(block.block_type)
        if mapped is None:
            mapped = "unknown"
            warnings.append(
                f"unmapped block_type {block.block_type!r} -> 'unknown'"
            )
        return mapped

    def _table_asset(
        self,
        element_id: str,
        block: BackendBlock,
        spans: tuple[EvidenceSpan, ...],
        container_id: str | None,
    ) -> TableAsset | None:
        """structure 网格 -> TableAsset；cell 可用 evidence_index 关联 span."""
        structure = block.structure or {}
        rows = structure.get("rows")
        cols = structure.get("cols")
        raw_cells = structure.get("cells") or []
        if not isinstance(rows, int) or not isinstance(cols, int) or not raw_cells:
            return None  # 网格证据不完整：不产资产，不伪造（SRS §7.4）

        has_header = any(bool(c.get("is_header")) for c in raw_cells)
        cells = tuple(
            TableCell(
                row_index=int(c["row_index"]),
                column_index=int(c["column_index"]),
                text=str(c.get("text", "")),
                row_span=int(c.get("row_span", 1)),
                column_span=int(c.get("column_span", 1)),
                formula=c.get("formula"),
                is_header=bool(c.get("is_header")),
                source_span_id=_cell_span_id(c, spans),
            )
            for c in raw_cells
        )
        return TableAsset(
            table_id=f"{element_id}-table",
            page_span_ids=(container_id,) if container_id else (),
            rows=rows,
            columns=cols,
            cells=cells,
            header_regions=((0, 0),) if has_header else (),
            confidence=Confidence(source="native"),
        )


def _cell_span_id(
    cell: dict, spans: tuple[EvidenceSpan, ...]
) -> str | None:
    """cell["evidence_index"] -> span id；无索引时回退首个 span."""
    if not spans:
        return None
    idx = cell.get("evidence_index")
    if isinstance(idx, int) and 0 <= idx < len(spans):
        return spans[idx].span_id
    return spans[0].span_id


# ---------------------------------------------------------------------------
# 模块级纯函数（heading 弹栈 / 关系 / style，与 M2 思路一致）
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


def _element_relations(
    prev_id: str | None, parent_id: str | None, element_id: str
) -> list[Relation]:
    out: list[Relation] = []
    if parent_id is not None:
        out.append(Relation(
            source_element_id=parent_id,
            target_element_id=element_id,
            relation_type="parent_of",
        ))
    if prev_id is not None:
        out.append(Relation(
            source_element_id=prev_id,
            target_element_id=element_id,
            relation_type="next_in_reading_order",
        ))
    return out


def _filtered_annotations(block: BackendBlock) -> dict[str, object]:
    """annotations 白名单（评审 MED：cells 网格已在 TableAsset 存一份，
    整包 dict(structure) 会让表格数据在 IR 中双份存储）。"""
    if not block.structure:
        return {}
    skip = {"cells", "rows", "cols"}  # 网格数据归 TableAsset
    return {k: v for k, v in block.structure.items() if k not in skip}


def _make_style(block: BackendBlock) -> dict[str, object]:
    style: dict[str, object] = {}
    if block.level is not None:
        style["level"] = block.level
    return style


__all__ = ["BaseNativeNormalizer"]
