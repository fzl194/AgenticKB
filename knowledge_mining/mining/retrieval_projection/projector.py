"""retrieval_unit_project 纯投影器（批次8 M2，24 号 §5.4）.

从 CompiledSegment 确定性编译类型化搜索表示：
- 纯函数、零 LLM、零数据库写入（暂存写入由门面/服务层负责）；
- 类型矩阵：paragraph→prose；table→table；table_row→table_row；
  code→code_block；list→list_group；formula→formula；
  figure_caption→figure_caption；heading/navigation→不单独成表示；
- 同源规则：table whole 与 row 是不同 canonical target，row 保留
  table container_ref；全部 id 确定性、可幂等重建。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from knowledge_mining.mining.contracts.retrieval_projection import (
    PROJECTOR_NAME,
    PROJECTOR_VERSION,
    RetrievalRepresentation,
)
from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment

# block_type → (representation_type, content_type)
# 词表以编译器实际产出为准（compiler.py：paragraph/list_item/code/quote/
# table/table_row/figure/heading/…）；list/figure_caption 为同义历史键保留。
_BLOCK_TYPE_MATRIX: Mapping[str, tuple[str, str]] = {
    "paragraph": ("prose", "paragraph"),
    "table": ("table", "table"),
    "table_row": ("table_row", "table_row"),
    "code": ("code_block", "code"),
    "list": ("list_group", "list"),
    "list_item": ("list_group", "list"),
    "formula": ("formula", "formula"),
    "figure": ("figure_caption", "figure"),
    "figure_caption": ("figure_caption", "figure_caption"),
}
# heading / navigation 及未知类型不单独形成正文表示（矩阵默认行为）

MAX_SECTION_DIRECT_TOKENS = 1600


def _document_representation(
    segments: Sequence[CompiledSegment],
    *,
    document_ref: str,
    snapshot_ref: str,
) -> RetrievalRepresentation:
    """文档级表示（24 号 §5.4 矩阵）：文件名/标题等来源事实，不做 LLM 摘要.

    标题取首个 heading 切片（可追溯），缺失时回落 document_ref 本身；
    canonical target = document。
    """
    title = next(
        (seg.raw_text for seg in segments if seg.block_type == "heading"),
        None,
    ) or document_ref
    target_ref = f"{document_ref}#document"
    representation_id = f"{snapshot_ref}:document:0"
    return RetrievalRepresentation(
        representation_id=representation_id,
        representation_type="document",
        content_type="document",
        content_text=title,
        structural_context="",
        target_type="document",
        target_ref=target_ref,
        canonical_evidence_id=representation_id,
        source_refs=(),
        container_ref=None,
        context_group_id=document_ref,
        ordinal=-1,
        facets={
            "document": document_ref,
            "content_type": "document",
        },
        provenance={
            "projector": PROJECTOR_NAME,
            "projector_version": PROJECTOR_VERSION,
        },
    )


def _breadcrumb(heading_chain: Sequence[tuple[int, str]]) -> str:
    return " > ".join(title for _level, title in heading_chain)


def _facets(
    *,
    document_ref: str,
    content_type: str,
    heading_chain: Sequence[tuple[int, str]],
) -> dict[str, Any]:
    facets: dict[str, Any] = {
        "document": document_ref,
        "content_type": content_type,
        "section_depth": len(heading_chain),
    }
    if heading_chain:
        facets["section_path"] = _breadcrumb(heading_chain)
    return facets


def _source_refs(segment: CompiledSegment) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        {"element_id": link.element_id, "evidence_span_ids": link.evidence_span_ids}
        for link in segment.links
    )


def _representation_for(
    segment: CompiledSegment,
    *,
    document_ref: str,
    snapshot_ref: str,
) -> RetrievalRepresentation | None:
    mapped = _BLOCK_TYPE_MATRIX.get(segment.block_type)
    if mapped is None:
        return None
    representation_type, content_type = mapped
    target_type = representation_type if representation_type != "prose" else "segment"

    metadata: Mapping[str, Any] = segment.metadata or {}
    table_ref = str(metadata.get("table_ref") or "")
    header = metadata.get("table_header") or ()
    # figure 切片的 caption 在 metadata.figure_caption；表格在
    # metadata.table_caption（compiler.py 惯例）。
    caption = str(
        metadata.get("caption")
        or metadata.get("figure_caption")
        or metadata.get("table_caption")
        or ""
    )

    if representation_type == "table_row":
        structural_context = " | ".join(
            part for part in (caption, "表头: " + "/".join(map(str, header))) if part
        )
        target_ref = (
            f"{document_ref}#table_row:{table_ref or segment.element_ids[0] if segment.element_ids else segment.segment_index}"
            f":{metadata.get('row_index', segment.segment_index)}"
        )
        container_ref = table_ref or None
    elif representation_type == "table":
        structural_context = " | ".join(
            part for part in (caption, "表头: " + "/".join(map(str, header))) if part
        )
        target_ref = f"{document_ref}#table:{table_ref or segment.segment_index}"
        container_ref = None
    else:
        # figure_caption 用原始 caption 作结构上下文（§5.4：原始 caption/附近
        # mention）；其余类型走标题面包屑。caption 缺失时不改变原行为。
        structural_context = (
            f"{caption} | {_breadcrumb(segment.heading_chain)}".strip(" |")
            if caption else _breadcrumb(segment.heading_chain)
        )
        target_ref = f"{document_ref}#seg:{segment.segment_index}"
        container_ref = None

    # table_row 的 raw_text 本身已是自描述行文本（compiler `_row_text`：
    # "列名=值；列名=值"——行脱离表头仍有语义），直接作为检索文本，不再
    # 二次编码（此前按 \t 切分重组成"列名为值"会产生"告警码为告警码=…"）。
    content_text = segment.raw_text

    # 27号审查修复（E2E 追溯发现）：representation_id 与 canonical 锚定
    # snapshot 而非 document_ref——同内容多文档共享快照时 doc_key 不同的
    # 重挖会改写 units 的 id，而 embeddings 仍挂旧 id，dense 联接断裂
    # （dense_ready=false / covered=0）。target_ref 保持文档锚定（展示与
    # within 过滤语义），文档归属由 asset_document_snapshot_links 提供。
    representation_id = (
        f"{snapshot_ref}:{representation_type}:{segment.segment_index}"
    )
    return RetrievalRepresentation(
        representation_id=representation_id,
        representation_type=representation_type,
        content_type=content_type,
        content_text=content_text,
        structural_context=structural_context,
        target_type=target_type,
        target_ref=target_ref,
        canonical_evidence_id=representation_id,
        source_refs=_source_refs(segment),
        container_ref=container_ref,
        context_group_id=(
            segment.heading_chain[-1][1] if segment.heading_chain else document_ref
        ),
        ordinal=segment.segment_index,
        facets=_facets(
            document_ref=document_ref,
            content_type=content_type,
            heading_chain=segment.heading_chain,
        ),
        provenance={
            "projector": PROJECTOR_NAME,
            "projector_version": PROJECTOR_VERSION,
            "source_segment_index": segment.segment_index,
        },
    )


def _section_representations(
    segments: Sequence[CompiledSegment],
    *,
    document_ref: str,
    snapshot_ref: str,
    max_direct_tokens: int = MAX_SECTION_DIRECT_TOKENS,
) -> tuple[RetrievalRepresentation, ...]:
    """真实章节标题 + 有界直接内容投影（不生成 LLM 摘要）."""
    by_path: dict[tuple[tuple[int, str], ...], list[CompiledSegment]] = {}
    for segment in segments:
        if segment.block_type in {"heading", "navigation"}:
            continue
        if not segment.heading_chain:
            continue
        by_path.setdefault(tuple(segment.heading_chain), []).append(segment)

    reps: list[RetrievalRepresentation] = []
    for order, (path, children) in enumerate(
        sorted(by_path.items(), key=lambda item: item[1][0].segment_index)
    ):
        title = path[-1][1]
        used = 0
        parts: list[str] = []
        for child in children:
            if used + (child.token_count or 0) > max_direct_tokens:
                break
            parts.append(child.raw_text)
            used += child.token_count or 0
        if not parts:
            continue
        target_ref = f"{document_ref}#section:{'/'.join(t for _l, t in path)}"
        representation_id = f"{snapshot_ref}:section:{order}"
        reps.append(
            RetrievalRepresentation(
                representation_id=representation_id,
                representation_type="section",
                content_type="section",
                content_text=f"{title}\n" + "\n".join(parts),
                structural_context=_breadcrumb(path[:-1]),
                target_type="section",
                target_ref=target_ref,
                canonical_evidence_id=representation_id,
                source_refs=(
                    {"segment_index": child.segment_index} for child in children
                ),
                context_group_id=title,
                ordinal=order,
                facets={
                    "document": document_ref,
                    "content_type": "section",
                    "section_path": _breadcrumb(path),
                    "section_depth": len(path),
                },
                provenance={
                    "projector": PROJECTOR_NAME,
                    "projector_version": PROJECTOR_VERSION,
                    "child_segment_indexes": [c.segment_index for c in children],
                },
            )
        )
    return tuple(reps)


def project_representations(
    segments: Iterable[CompiledSegment],
    *,
    document_ref: str,
    snapshot_ref: str,
    include_sections: bool = False,
) -> tuple[RetrievalRepresentation, ...]:
    """从编译切片确定性投影类型化搜索表示（纯函数）."""
    materialized = tuple(segments)
    reps: list[RetrievalRepresentation] = [
        # 文档级表示始终生成（§5.4 矩阵默认 FTS/dense/returnable 全开）
        _document_representation(
            materialized, document_ref=document_ref, snapshot_ref=snapshot_ref,
        )
    ]
    for segment in materialized:
        rep = _representation_for(
            segment, document_ref=document_ref, snapshot_ref=snapshot_ref
        )
        if rep is not None:
            reps.append(rep)
    if include_sections:
        reps.extend(
            _section_representations(
                materialized,
                document_ref=document_ref,
                snapshot_ref=snapshot_ref,
            )
        )
    return tuple(reps)


__all__ = ["project_representations"]
