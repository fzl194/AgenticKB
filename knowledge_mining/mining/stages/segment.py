"""Segmentation module: split SectionNode tree into L0 RawSegmentData.

v2.0 key points:
- Headings are metadata (section_title + section_path), NOT independent segments
- Structural relations are built in relations stage from section_path hierarchy
- structure_json preserves table columns/rows from ContentBlock.structure
- source_offsets_json includes parser, block_index, line_start, line_end
- Entity extraction and role classification are NOT done here — deferred to enrich stage
- Post-processing merges small segments (<100 tokens) with adjacent intro+list/table pairs (Unstructured CompositeElement)
- Cross-section orphan absorption: fragments below min_chunk_tokens merge into parent section (LlamaIndex AutoMerging)
- Structural breadcrumb: section path hierarchy injected into metadata for free context (Anthropic contextual retrieval)
- Text cleanup: strip markdown bold, <br>, image refs, and inline links from raw_text
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from knowledge_mining.mining.contracts.models import ContentBlock, DocumentProfile, RawSegmentData, SectionNode
from knowledge_mining.mining.infra.hash_utils import content_hash, normalized_hash
from knowledge_mining.mining.infra.text_utils import token_count
from knowledge_mining.mining.infra.text_utils import split_sentences

# Text cleanup patterns — applied to raw_text after block joining
_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
_BR_TAG_RE = re.compile(r'<br\s*/?>', re.IGNORECASE)
_IMAGE_REF_RE = re.compile(r'!\[.*?\]\([^\)]*\)')
_IMAGE_REF_STYLE_RE = re.compile(r'!\[.*?\]\[.*?\]')
_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\((?:[^()]+|\([^()]*\))*\)')
_COLLAPSE_RE = re.compile(r'\n{3,}')


def _clean_segment_text(text: str) -> str:
    """Clean residual markdown/HTML from segment text."""
    text = _BOLD_RE.sub(r'\1', text)
    text = _BR_TAG_RE.sub('\n', text)
    text = _IMAGE_REF_RE.sub('', text)
    text = _IMAGE_REF_STYLE_RE.sub('', text)
    text = _MD_LINK_RE.sub(r'\1', text)
    text = _COLLAPSE_RE.sub('\n\n', text)
    return text.strip()


class DefaultSegmenter:
    """Default segmenter wrapping segment_document() for PipelineConfig."""

    stage_name = "segment"
    stage_version = "2"

    def segment(
        self,
        tree: SectionNode,
        profile: DocumentProfile,
        **kwargs: Any,
    ) -> list[RawSegmentData]:
        return segment_document(
            tree, profile,
            parser_name=kwargs.get("parser_name", "unknown"),
            domain_profile=kwargs.get("domain_profile"),
            policy_override=kwargs.get("policy_override"),
        )

_SCHEMA_BLOCK_TYPES = {
    "paragraph", "table", "list", "code", "blockquote",
    "html_table", "raw_html", "image", "unknown",
}

# Merge thresholds — module-level named constants for discoverability
_MERGE_MAX_TOKENS = 512
_SPLIT_MAX_TOKENS = 512
_TABLE_MIN_INDEPENDENT_TOKENS = 300

# Emitted as their own segment (not grouped with surrounding paragraphs).
_INDEPENDENT_BLOCK_TYPES = frozenset({
    "table", "html_table", "code", "list", "blockquote", "image",
})

# Never absorb orphans into / merge across these (image joins table/code).
_NON_MERGEABLE_BLOCK_TYPES = frozenset({
    "table", "html_table", "code", "image",
})


@dataclass(frozen=True)
class SegmentPolicyOverride:
    min_segment_tokens: int
    max_segment_tokens: int
    structural_context_mode: str
    merge_small_segments: bool = True
    absorb_child_orphans: bool = True
    merge_lead_into_child: bool = True
    merge_small_min_tokens: int | None = None
    merge_small_max_tokens: int | None = None

# Block type priority for merged segments (higher = dominant)
_BLOCK_TYPE_PRIORITY: dict[str, int] = {
    "image": 5,
    "table": 4, "html_table": 4,
    "list": 3,
    "code": 2,
    "blockquote": 1,
    "paragraph": 1,
    "raw_html": 1,
    "unknown": 0,
}


def segment_document(
    doc_root: SectionNode,
    profile: DocumentProfile,
    *,
    parser_name: str = "unknown",
    domain_profile: Any | None = None,
    policy_override: SegmentPolicyOverride | None = None,
) -> list[RawSegmentData]:
    """Split document section tree into raw segments.

    Headings are propagated via section_title and section_path on content segments.
    The relations stage builds hierarchy from section_path, not from heading segments.

    Entity extraction and role classification are deferred to the enrich stage.
    Segments are produced with default semantic_role="unknown" and empty entity_refs_json.

    Pipeline phases:
    1. Walk sections → raw segments
    2. Merge small segments within same section (Unstructured CompositeElement)
    3. Absorb cross-section orphans into parent sections (LlamaIndex AutoMerging)
    4. Split large segments at paragraph/sentence boundaries
    5. Inject structural breadcrumb context (Anthropic contextual retrieval, free)
    """
    # Resolve thresholds from domain_profile if available
    policy = None
    if domain_profile is not None and hasattr(domain_profile, "retrieval_policy"):
        policy = domain_profile.retrieval_policy

    min_chunk = policy.min_chunk_tokens if policy else _DEFAULT_MIN_CHUNK_TOKENS
    max_chunk = policy.max_chunk_tokens if policy else _SPLIT_MAX_TOKENS
    cross_section = policy.cross_section_merge if policy else True
    ctx_mode = policy.structural_context_mode if policy else "breadcrumb"
    merge_small = True
    absorb_orphans = cross_section
    merge_lead = cross_section
    merge_min = 100
    merge_max = _MERGE_MAX_TOKENS
    if policy_override is not None:
        min_chunk = policy_override.min_segment_tokens
        max_chunk = policy_override.max_segment_tokens
        ctx_mode = policy_override.structural_context_mode
        merge_small = policy_override.merge_small_segments
        absorb_orphans = policy_override.absorb_child_orphans
        merge_lead = policy_override.merge_lead_into_child
        merge_min = policy_override.merge_small_min_tokens or min_chunk
        merge_max = policy_override.merge_small_max_tokens or max_chunk

    # Phase 1: Walk section tree → raw segments
    segments: list[RawSegmentData] = []
    _walk_sections(doc_root, profile.document_key, [], segments, parser_name)

    # Phase 2: Merge small segments within same section
    if merge_small:
        segments = _merge_small_segments(
            segments, min_tokens=merge_min, max_tokens=merge_max
        )

    # Phase 3: Absorb cross-section orphans (LlamaIndex AutoMerging pattern)
    if absorb_orphans:
        segments = _absorb_orphan_segments(segments, min_chunk, max_chunk)

    # Phase 3.5: Fold small lead-in paragraphs FORWARD into the next segment.
    # Fills the gap left by Phase 2 (same-section only) and Phase 3 (ancestor
    # only): a short intro like "总体分为如下三个部分：" whose content lives in a
    # CHILD subsection.
    if merge_lead:
        segments = _merge_lead_in_segments(segments, min_chunk, max_chunk)

    # Phase 4: Split large segments at paragraph/sentence boundaries
    segments = _split_large_segments(segments, max_tokens=max_chunk)

    # Phase 5: Inject structural breadcrumb (free context, no LLM)
    if ctx_mode == "breadcrumb":
        doc_title = doc_root.title or ""
        segments = _inject_structural_context(segments, doc_title)

    # Re-index
    return [
        RawSegmentData(
            document_key=s.document_key,
            segment_index=idx,
            block_type=s.block_type,
            semantic_role=s.semantic_role,
            section_path=s.section_path,
            section_title=s.section_title,
            raw_text=s.raw_text,
            normalized_text=s.normalized_text,
            content_hash=s.content_hash,
            normalized_hash=s.normalized_hash,
            token_count=s.token_count,
            structure_json=s.structure_json,
            source_offsets_json=s.source_offsets_json,
            entity_refs_json=s.entity_refs_json,
            metadata_json=s.metadata_json,
        )
        for idx, s in enumerate(segments)
    ]


def _walk_sections(
    node: SectionNode,
    document_key: str,
    parent_path: list[dict[str, Any]],
    segments: list[RawSegmentData],
    parser_name: str,
) -> None:
    """Recursively walk section tree, creating segments."""
    current_path = list(parent_path)
    if node.title and node.level > 0:
        current_path.append({"title": node.title, "level": node.level})

    current_group: list[ContentBlock] = []
    block_index = 0

    for block in node.blocks:
        if block.block_type == "heading":
            # Heading blocks are metadata carried by section_title/section_path.
            # Skip them here — their text is already in the section hierarchy.
            continue
        elif block.block_type in _INDEPENDENT_BLOCK_TYPES:
            if current_group:
                segments.append(
                    _make_segment(
                        document_key, current_path, node, current_group,
                        block_index, parser_name,
                    )
                )
                block_index += 1
                current_group = []
            segments.append(
                _make_segment(
                    document_key, current_path, node, [block],
                    block_index, parser_name,
                )
            )
            block_index += 1
        else:
            current_group.append(block)

    if current_group:
        segments.append(
            _make_segment(
                document_key, current_path, node, current_group,
                block_index, parser_name,
            )
        )

    for child in node.children:
        _walk_sections(child, document_key, current_path, segments, parser_name)


def _merge_small_segments(
    segments: list[RawSegmentData],
    min_tokens: int = 100,
    max_tokens: int = _MERGE_MAX_TOKENS,
) -> list[RawSegmentData]:
    """Merge small segments into adjacent segments (Unstructured.io CompositeElement pattern).

    Rules:
    1. Segments < min_tokens are candidates for merging
    2. A short paragraph can merge with the following list/table in the same section
       (intro paragraph + content list/table → single composite segment)
    3. A short segment can also merge into the previous segment in the same section
    4. Merged result must not exceed 512 tokens
    5. Block type priority: table > list > paragraph
    6. Tables > 300 tokens stay independent
    """
    if not segments:
        return segments

    merged: list[RawSegmentData] = [segments[0]]

    for seg in segments[1:]:
        prev = merged[-1]
        same_section = seg.section_path == prev.section_path

        if not same_section:
            merged.append(seg)
            continue

        # Guard against None token_count — treat as 0 (very short, mergeable)
        prev_tc = prev.token_count if prev.token_count is not None else 0
        seg_tc = seg.token_count if seg.token_count is not None else 0

        # Try merge: short prev-paragraph + current list/table (intro→content pattern)
        intro_merge = (
            prev.block_type == "paragraph"
            and prev_tc < min_tokens
            and seg.block_type in ("list", "table", "html_table")
            and (prev_tc + seg_tc) <= max_tokens
            and not (seg.block_type in ("table", "html_table") and seg_tc > _TABLE_MIN_INDEPENDENT_TOKENS)
        )

        # Try merge: short current segment into previous (never merge tables/code/image)
        backward_merge = (
            seg_tc < min_tokens
            and seg.block_type not in _NON_MERGEABLE_BLOCK_TYPES
            and (prev_tc + seg_tc) <= max_tokens
            and prev.block_type not in _NON_MERGEABLE_BLOCK_TYPES
        )

        if intro_merge or backward_merge:
            new_text = prev.raw_text + "\n\n" + seg.raw_text
            # Block type priority: table > list > paragraph
            new_block_type = _pick_block_type(prev.block_type, seg.block_type)
            # Structure: merge both
            new_structure = {**(prev.structure_json or {}), **(seg.structure_json or {})}
            merged[-1] = RawSegmentData(
                document_key=prev.document_key,
                segment_index=0,  # re-indexed later
                block_type=new_block_type,
                semantic_role=prev.semantic_role,
                section_path=prev.section_path,
                section_title=prev.section_title,
                raw_text=new_text,
                normalized_text=new_text.lower().strip(),
                content_hash=content_hash(new_text),
                normalized_hash=normalized_hash(new_text),
                token_count=token_count(new_text),
                structure_json=new_structure,
                source_offsets_json=prev.source_offsets_json,
                entity_refs_json=prev.entity_refs_json,
                metadata_json=prev.metadata_json,
            )
        else:
            merged.append(seg)

    return merged


# Default threshold for cross-section orphan absorption
_DEFAULT_MIN_CHUNK_TOKENS = 128


def _absorb_orphan_segments(
    segments: list[RawSegmentData],
    min_tokens: int = _DEFAULT_MIN_CHUNK_TOKENS,
    max_tokens: int = 512,
) -> list[RawSegmentData]:
    """Absorb cross-section orphans into parent section segments.

    After Phase 2 (within-section merge), some segments remain below
    min_tokens but can't merge because they're in different sections.
    This phase walks up the section_path hierarchy to find a merge candidate.

    Algorithm (LlamaIndex AutoMerging inspired):
    1. Build section_path_tuple → [segment_index] mapping
    2. Identify orphans: token_count < min_tokens AND block_type not structural
    3. For each orphan (descending order to avoid index shifts):
       a. Walk up section_path hierarchy (pop last element)
       b. At each ancestor level, find nearest segment by index distance
       c. If combined tokens ≤ max_tokens, merge and remove orphan
    4. Re-index remaining segments
    """
    if not segments:
        return segments

    # Build section_path → [index] index
    path_index: dict[tuple, list[int]] = {}
    for idx, seg in enumerate(segments):
        path_key = tuple(p.get("title", "") for p in seg.section_path)
        path_index.setdefault(path_key, []).append(idx)

    # Identify orphans
    orphan_indices: list[int] = []
    for idx, seg in enumerate(segments):
        tc = seg.token_count if seg.token_count is not None else 0
        if (tc < min_tokens
                and seg.block_type not in _NON_MERGEABLE_BLOCK_TYPES):
            orphan_indices.append(idx)

    if not orphan_indices:
        return segments

    # Process orphans in descending order to avoid index shifts
    # (we're marking removals, not actually removing yet)
    removed: set[int] = set()
    merge_log: list[tuple[int, int]] = []  # (orphan_idx, target_idx)

    for orphan_idx in reversed(orphan_indices):
        if orphan_idx in removed:
            continue

        orphan = segments[orphan_idx]
        orphan_tc = orphan.token_count if orphan.token_count is not None else 0
        section_path = list(orphan.section_path)

        # Walk up section_path hierarchy
        merged = False
        while len(section_path) > 0:
            section_path = section_path[:-1]
            parent_key = tuple(p.get("title", "") for p in section_path)

            candidate_indices = path_index.get(parent_key, [])
            # Filter out already-removed segments
            candidate_indices = [i for i in candidate_indices if i not in removed and i != orphan_idx]

            if not candidate_indices:
                continue

            # Find nearest candidate by index distance
            best_idx = min(candidate_indices, key=lambda i: abs(i - orphan_idx))
            candidate = segments[best_idx]
            candidate_tc = candidate.token_count if candidate.token_count is not None else 0

            # Check merge feasibility
            if candidate_tc + orphan_tc > max_tokens:
                continue
            # Never merge into table/code/image blocks
            if candidate.block_type in _NON_MERGEABLE_BLOCK_TYPES:
                continue

            # Merge orphan into candidate
            new_text = candidate.raw_text + "\n\n" + orphan.raw_text
            new_block_type = _pick_block_type(candidate.block_type, orphan.block_type)
            new_structure = {**(candidate.structure_json or {}), **(orphan.structure_json or {})}
            meta = dict(candidate.metadata_json)
            meta["merged_from_orphan"] = True
            meta["orphan_section_title"] = orphan.section_title

            segments[best_idx] = RawSegmentData(
                document_key=candidate.document_key,
                segment_index=candidate.segment_index,
                block_type=new_block_type,
                semantic_role=candidate.semantic_role,
                section_path=candidate.section_path,
                section_title=candidate.section_title,
                raw_text=new_text,
                normalized_text=new_text.lower().strip(),
                content_hash=content_hash(new_text),
                normalized_hash=normalized_hash(new_text),
                token_count=token_count(new_text),
                structure_json=new_structure,
                source_offsets_json=candidate.source_offsets_json,
                entity_refs_json=candidate.entity_refs_json,
                metadata_json=meta,
            )
            removed.add(orphan_idx)
            merge_log.append((orphan_idx, best_idx))
            merged = True
            break

    if not removed:
        return segments

    # Filter out removed orphans
    return [seg for idx, seg in enumerate(segments) if idx not in removed]


def _path_titles(section_path: list[dict[str, Any]]) -> tuple[str, ...]:
    """Extract the ordered title tuple from a section_path."""
    return tuple(p.get("title", "") for p in section_path)


def _is_lineage(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True when one path is a prefix of the other (ancestor/descendant)."""
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def _prepend_text(seg: RawSegmentData, prefix_text: str) -> RawSegmentData:
    """Return a copy of seg with prefix_text prepended, hashes/counts recomputed."""
    new_text = prefix_text + "\n\n" + seg.raw_text
    meta = dict(seg.metadata_json)
    meta["merged_lead_in"] = True
    return RawSegmentData(
        document_key=seg.document_key,
        segment_index=seg.segment_index,
        block_type=seg.block_type,
        semantic_role=seg.semantic_role,
        section_path=seg.section_path,
        section_title=seg.section_title,
        raw_text=new_text,
        normalized_text=new_text.lower().strip(),
        content_hash=content_hash(new_text),
        normalized_hash=normalized_hash(new_text),
        token_count=token_count(new_text),
        structure_json=seg.structure_json,
        source_offsets_json=seg.source_offsets_json,
        entity_refs_json=seg.entity_refs_json,
        metadata_json=meta,
    )


def _merge_lead_in_segments(
    segments: list[RawSegmentData],
    min_tokens: int,
    max_tokens: int,
) -> list[RawSegmentData]:
    """Fold a small lead-in paragraph forward into the following segment.

    Phase 2 only merges within a section; Phase 3 only merges UP into ancestor
    sections. Neither handles a short intro paragraph whose content lives in a
    CHILD subsection — e.g. "网络搬迁流程总体分为如下三个部分：" immediately
    followed by ## subsections.

    Rules:
    1. A lead-in candidate is a paragraph segment with token_count < min_tokens.
    2. It merges into the NEXT segment only when they share a section lineage
       (one section_path is a prefix of the other) but are NOT the same section
       (same-section cases are already handled by Phase 2).
    3. Never prepend prose into a table/code block; combined ≤ max_tokens.
    """
    if len(segments) < 2:
        return segments

    result: list[RawSegmentData] = []
    pending: RawSegmentData | None = None

    for seg in segments:
        if pending is not None:
            p_tc = pending.token_count if pending.token_count is not None else 0
            s_tc = seg.token_count if seg.token_count is not None else 0
            pending_path = _path_titles(pending.section_path)
            seg_path = _path_titles(seg.section_path)
            can_merge = (
                seg.block_type not in _NON_MERGEABLE_BLOCK_TYPES
                and (p_tc + s_tc) <= max_tokens
                and pending_path != seg_path
                and _is_lineage(pending_path, seg_path)
            )
            if can_merge:
                seg = _prepend_text(seg, pending.raw_text)
            else:
                result.append(pending)
            pending = None

        tc = seg.token_count if seg.token_count is not None else 0
        if tc < min_tokens and seg.block_type == "paragraph":
            pending = seg
            continue
        result.append(seg)

    if pending is not None:
        result.append(pending)

    return result


def _inject_structural_context(
    segments: list[RawSegmentData],
    doc_title: str,
) -> list[RawSegmentData]:
    """Inject structural breadcrumb into each segment's metadata_json.

    Builds a breadcrumb like "[doc_title] > [section1] > [section2] > ..."
    from the section_path. This provides FREE context (no LLM needed)
    following the Anthropic contextual retrieval pattern.

    The breadcrumb is consumed by retrieval_units stage to enrich search_text.
    """
    result: list[RawSegmentData] = []
    for seg in segments:
        parts: list[str] = []
        for p in seg.section_path:
            t = p.get("title", "")
            if t and t not in parts:
                parts.append(t)
        # Prepend doc_title if not already covered by section_path
        if doc_title and doc_title not in parts:
            parts.insert(0, doc_title)
        breadcrumb = " > ".join(parts) if parts else ""

        if not breadcrumb:
            result.append(seg)
            continue

        meta = dict(seg.metadata_json)
        meta["structural_context"] = breadcrumb
        result.append(RawSegmentData(
            document_key=seg.document_key,
            segment_index=seg.segment_index,
            block_type=seg.block_type,
            semantic_role=seg.semantic_role,
            section_path=seg.section_path,
            section_title=seg.section_title,
            raw_text=seg.raw_text,
            normalized_text=seg.normalized_text,
            content_hash=seg.content_hash,
            normalized_hash=seg.normalized_hash,
            token_count=seg.token_count,
            structure_json=seg.structure_json,
            source_offsets_json=seg.source_offsets_json,
            entity_refs_json=seg.entity_refs_json,
            metadata_json=meta,
        ))
    return result


def _split_large_segments(
    segments: list[RawSegmentData],
    max_tokens: int = 512,
) -> list[RawSegmentData]:
    """Split segments exceeding max_tokens at paragraph/sentence boundaries.

    Strategy:
    1. Split at paragraph boundaries (\\n\\n)
    2. If a single paragraph still exceeds the limit, split at sentence boundaries
    3. Preserve section_path, section_title and other metadata on each sub-segment
    """
    if not segments:
        return segments

    result: list[RawSegmentData] = []
    for seg in segments:
        tc = seg.token_count if seg.token_count is not None else 0
        if tc <= max_tokens:
            result.append(seg)
            continue

        sub_texts = _split_text_by_paragraphs(seg.raw_text, max_tokens)
        for sub in sub_texts:
            result.append(_clone_segment_with_text(seg, sub))

    return result


def _split_text_by_paragraphs(text: str, max_tokens: int) -> list[str]:
    """Split text at paragraph boundaries, then at sentence boundaries if needed."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_tc = 0

    for para in paragraphs:
        para_tc = token_count(para)

        # If a single paragraph exceeds the limit, split it by sentences
        if para_tc > max_tokens:
            # Flush current buffer first
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_tc = 0
            # Split paragraph into sentences
            chunks.extend(_split_paragraph_by_sentences(para, max_tokens))
            continue

        if current_tc + para_tc > max_tokens and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_tc = para_tc
        else:
            current.append(para)
            current_tc += para_tc

    if current:
        chunks.append("\n\n".join(current))

    return [c for c in chunks if c.strip()]


def _split_paragraph_by_sentences(text: str, max_tokens: int) -> list[str]:
    """Split a single paragraph at sentence boundaries."""
    sentences = split_sentences(text)
    if not sentences:
        return [text] if text.strip() else []

    chunks: list[str] = []
    current: list[str] = []
    current_tc = 0

    for sent in sentences:
        sent_tc = token_count(sent)
        if current_tc + sent_tc > max_tokens and current:
            chunks.append("".join(current))
            current = [sent]
            current_tc = sent_tc
        else:
            current.append(sent)
            current_tc += sent_tc

    if current:
        chunks.append("".join(current))

    return [c for c in chunks if c.strip()]


def _clone_segment_with_text(seg: RawSegmentData, new_text: str) -> RawSegmentData:
    """Create a new RawSegmentData with replaced text and recomputed hashes/counts."""
    norm = new_text.lower().strip()
    return RawSegmentData(
        document_key=seg.document_key,
        segment_index=0,  # re-indexed by caller
        block_type=seg.block_type,
        semantic_role=seg.semantic_role,
        section_path=seg.section_path,
        section_title=seg.section_title,
        raw_text=new_text,
        normalized_text=norm,
        content_hash=content_hash(new_text),
        normalized_hash=normalized_hash(new_text),
        token_count=token_count(new_text),
        structure_json=seg.structure_json,
        source_offsets_json=seg.source_offsets_json,
        entity_refs_json=seg.entity_refs_json,
        metadata_json=seg.metadata_json,
    )


def _pick_block_type(a: str, b: str) -> str:
    """Pick dominant block type using _BLOCK_TYPE_PRIORITY."""
    pa = _BLOCK_TYPE_PRIORITY.get(a, 0)
    pb = _BLOCK_TYPE_PRIORITY.get(b, 0)
    return a if pa >= pb else b


def _make_segment(
    document_key: str,
    section_path: list[dict[str, Any]],
    section: SectionNode,
    blocks: list[ContentBlock],
    block_index: int,
    parser_name: str,
) -> RawSegmentData:
    """Create a RawSegmentData from a group of content blocks.

    semantic_role defaults to "unknown" — enrich stage will assign the real role.
    entity_refs_json defaults to [] — enrich stage will populate.
    """
    primary_block = next((b for b in blocks if b.block_type != "heading"), None)
    if primary_block is None:
        # All blocks are headings — use the first one
        primary_block = blocks[0] if blocks else None
    block_type = _schema_block_type(primary_block.block_type if primary_block else "unknown")

    # Image blocks: keep caption text as-is (may be empty before VLM). Do not run
    # markdown image-ref stripping that would erase intentional captions later.
    if block_type == "image":
        raw_text = "\n\n".join(b.text for b in blocks).strip()
    else:
        raw_text = _clean_segment_text("\n\n".join(b.text for b in blocks))
    norm_text = raw_text.lower().strip()

    structure_json = _extract_structure_info(blocks)

    # Empty image captions still need stable distinct hashes (based on image bytes).
    if block_type == "image" and not raw_text:
        seed = (
            structure_json.get("image_sha256")
            or structure_json.get("image_path")
            or f"{document_key}:{block_index}"
        )
        hash_src = f"image:{seed}"
        ch = content_hash(hash_src)
        nh = normalized_hash(hash_src)
        tc = 0
    else:
        ch = content_hash(raw_text)
        nh = normalized_hash(raw_text)
        tc = token_count(raw_text)

    line_start = None
    line_end = None
    for b in blocks:
        if b.line_start is not None:
            if line_start is None or b.line_start < line_start:
                line_start = b.line_start
        if b.line_end is not None:
            if line_end is None or b.line_end > line_end:
                line_end = b.line_end

    source_offsets: dict[str, Any] = {"parser": parser_name, "block_index": block_index}
    if line_start is not None:
        source_offsets["line_start"] = line_start
    if line_end is not None:
        source_offsets["line_end"] = line_end

    return RawSegmentData(
        document_key=document_key,
        segment_index=0,
        block_type=block_type,
        semantic_role="unknown",
        section_path=section_path,
        section_title=section.title,
        raw_text=raw_text,
        normalized_text=norm_text,
        content_hash=ch,
        normalized_hash=nh,
        token_count=tc,
        structure_json=structure_json,
        source_offsets_json=source_offsets,
        entity_refs_json=[],
        metadata_json={},
    )


def _extract_structure_info(blocks: list[ContentBlock]) -> dict:
    """Extract structural metadata from blocks."""
    info: dict = {}
    for block in blocks:
        if block.block_type == "table":
            if block.structure:
                info.update(block.structure)
            else:
                parts = block.text.split(" | ")
                info["col_count"] = len(parts)
        elif block.block_type == "html_table":
            info["kind"] = "html_table"
            info["raw_html_preserved"] = True
            info["row_count"] = max(1, block.text.lower().count("<tr"))
            info["col_count"] = max(1, block.text.lower().count("<td") // max(1, block.text.lower().count("<tr")))
        elif block.block_type == "code":
            if block.structure:
                info.update(block.structure)
            elif block.language:
                info["kind"] = "code_block"
                info["language"] = block.language
        elif block.block_type == "list":
            if block.structure:
                info.update(block.structure)
            else:
                items = block.text.split("; ")
                info["ordered"] = False
                info["items"] = items
                info["item_count"] = len(items)
        elif block.block_type == "paragraph":
            info["paragraph_count"] = info.get("paragraph_count", 0) + 1
        elif block.block_type == "image":
            if block.structure:
                info.update(block.structure)
            else:
                info.setdefault("kind", "pdf_image")
    return info


def _schema_block_type(block_type: str) -> str:
    if block_type in _SCHEMA_BLOCK_TYPES:
        return block_type
    return "unknown"
