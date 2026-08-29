"""hierarchical_summary_generate 执行层（批次8 M3，24 号 §5.6）.

标题树自底向上的 section/document 摘要：``summary_alias``（derived，
returnable=False，绑定 source target refs）；非 RAPTOR（无聚类/跨层路由）。
LLM 失败只 summary capability degraded，不阻断基础资产。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from knowledge_mining.mining.contracts.retrieval_projection import (
    PROJECTOR_VERSION,
    RetrieRepresentation,
)
from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment

SUMMARY_VERSION = "hier-summary-1"
_MIN_SECTION_TOKENS = 120


@dataclass(frozen=True)
class SummaryOutcome:
    aliases: tuple[RetrieRepresentation, ...]
    skipped_sections: int
    llm_failures: int
    degraded: bool


def _summary_alias(
    *, document_ref: str, snapshot_ref: str, target_type: str, target_ref: str,
    title: str, summary_text: str, source_refs: tuple[Mapping, ...], ordinal: int,
) -> RetrieRepresentation:
    return RetrieRepresentation(
        representation_id=f"{document_ref}:{snapshot_ref}:summary:{ordinal}",
        representation_type="summary_alias",
        content_type="summary_alias",
        content_text=f"{title}：{summary_text}",
        structural_context="",
        target_type=target_type,
        target_ref=target_ref,
        canonical_evidence_id=target_ref,
        source_refs=source_refs,
        lexical_eligible=True,
        dense_eligible=True,
        returnable=False,  # derived，默认不可作逐字证据
        facets={"document": document_ref, "content_type": "summary_alias"},
        provenance={
            "projector": "hierarchical_summary_generate",
            "projector_version": SUMMARY_VERSION,
            "derived": True,
            "source_targets": [dict(ref) for ref in source_refs],
        },
    )


class HierarchicalSummaryFacade:
    """同步门面：标题树自底向上摘要（section → document）."""

    def __init__(self, *, segment_store: Any, alias_store: Any, summarizer: Any,
                 min_section_tokens: int = _MIN_SECTION_TOKENS) -> None:
        self._segments = segment_store
        self._aliases = alias_store
        self._summarizer = summarizer
        self._min_tokens = min_section_tokens

    def generate_for_snapshot(
        self, *, snapshot_id: str | None, params: Mapping[str, Any]
    ) -> SummaryOutcome:
        if not snapshot_id:
            return SummaryOutcome((), 0, 0, False)

        from .async_bridge import run_sync

        segments: tuple[CompiledSegment, ...] = run_sync(
            self._segments.list_for_snapshot(snapshot_id)
        )
        by_path: dict[tuple[tuple[int, str], ...], list[CompiledSegment]] = {}
        min_tokens = int(params.get("minSectionTokens", self._min_tokens))
        document_ref = str(params.get("documentRef") or snapshot_id)
        for segment in segments:
            if not segment.heading_chain:
                by_path.setdefault((), []).append(segment)
            else:
                by_path.setdefault(tuple(segment.heading_chain), []).append(segment)

        aliases: list[RetrieRepresentation] = []
        skipped = failures = 0
        ordinal = 0
        # 自底向上：深层 section 先于浅层；document 最后
        for path in sorted(by_path, key=lambda p: (-len(p), str(p))):
            children = by_path[path]
            tokens = sum(child.token_count or 0 for child in children)
            if tokens < min_tokens:
                skipped += 1
                continue
            title = path[-1][1] if path else document_ref
            try:
                summary_text = str(
                    self._summarizer.summarize(
                        title, [child.raw_text for child in children]
                    )
                ).strip()
            except Exception:  # noqa: BLE001
                failures += 1
                continue
            if not summary_text:
                skipped += 1
                continue
            target_ref = (
                f"{document_ref}#section:{'/'.join(t for _l, t in path)}"
                if path else f"{document_ref}#document"
            )
            aliases.append(
                _summary_alias(
                    document_ref=document_ref, snapshot_ref=snapshot_id,
                    target_type="section" if path else "document",
                    target_ref=target_ref, title=title, summary_text=summary_text,
                    source_refs=tuple(
                        {"segment_index": child.segment_index} for child in children
                    ),
                    ordinal=ordinal,
                )
            )
            ordinal += 1

        degraded = failures > 0
        if aliases and self._aliases is not None:
            run_sync(
                self._aliases.replace_for_snapshot(
                    snapshot_id, tuple(aliases), SUMMARY_VERSION,
                    document_key=document_ref,
                )
            )
        return SummaryOutcome(tuple(aliases), skipped, failures, degraded)


__all__ = [
    "SUMMARY_VERSION",
    "HierarchicalSummaryFacade",
    "SummaryOutcome",
]
