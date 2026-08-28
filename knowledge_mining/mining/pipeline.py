"""Hot-pluggable pipeline architecture for Mining v1.2.

Defines:
- DocumentContext: per-document immutable pipeline state
- Segmenter Protocol
- PipelineConfig: composable pipeline configuration
- MiningPipeline: orchestrates per-document processing (sequential)
- StreamingPipeline: queue-based parallel pipeline (stages run concurrently)
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, replace
from queue import Queue
from threading import Thread
from typing import Any, Callable

from knowledge_mining.mining.contracts.models import (
    DocumentProfile,
    RawFileData,
    RawSegmentData,
    RetrievalUnitData,
    SectionNode,
    SegmentRelationData,
)
from knowledge_mining.mining.contracts.protocols import Segmenter
from knowledge_mining.mining.snapshot import select_or_create_snapshot
from knowledge_mining.mining.workflow.operators.options import (
    DiscourseOptions,
    EmbeddingOptions,
    EnrichOptions,
    EntityExtractOptions,
    EntityResolveOptions,
    ParseSegmentOptions,
    RetrievalUnitOptions,
)

logger = logging.getLogger(__name__)


def _parse_context(raw: RawFileData, cfg: "PipelineConfig") -> dict[str, Any]:
    """Build parser context: file path + run-scoped image dump dir when needed."""
    ctx: dict[str, Any] = {"file_path": raw.file_path}
    meta = raw.metadata_json if isinstance(raw.metadata_json, dict) else {}
    if meta.get("source_format"):
        ctx["source_format"] = meta["source_format"]
    if meta.get("fetch_remote_images") or getattr(cfg, "fetch_remote_images", False):
        ctx["fetch_remote_images"] = True
    if cfg.run_id:
        from knowledge_mining.mining.infra.image_assets import IMAGE_CAPABLE_FILE_TYPES
        from knowledge_mining.mining.infra.run_workdir import resolve_run_image_dir

        if raw.file_type in IMAGE_CAPABLE_FILE_TYPES:
            doc_key = raw.relative_path or raw.file_name or "doc"
            ctx["image_dir"] = str(resolve_run_image_dir(cfg.run_id, doc_key))
    return ctx


# ---------------------------------------------------------------------------
# Per-document context (immutable between stages)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DocumentContext:
    """Per-document pipeline state, immutable between stages."""

    raw_file: RawFileData | None = None
    profile: DocumentProfile | None = None
    tree: SectionNode | None = None
    segments: tuple[RawSegmentData, ...] = ()
    relations: tuple[SegmentRelationData, ...] = ()
    seg_ids: dict[str, str] = field(default_factory=dict)
    retrieval_units: tuple[RetrievalUnitData, ...] = ()
    error: str | None = None
    # 解析器"软失败"的原因（返回 None 而非抛异常）。与 error 不同：error → 文档记
    # 为失败；parse_error 只是让"跳过"能说清是为什么跳过。
    parse_error: str | None = None
    run_document_id: str | None = None
    sequence_id: int = 0

    # Phase 1c fields (set by Phase 1a, consumed by db_write_stage)
    action: str | None = None
    existing_doc: dict | None = None
    embeddings: tuple[dict, ...] = ()
    document_id: str | None = None
    snapshot_id: str | None = None

    def with_updates(self, **kwargs: Any) -> DocumentContext:
        """Return a new DocumentContext with specified fields replaced."""
        current = {
            "raw_file": self.raw_file,
            "profile": self.profile,
            "tree": self.tree,
            "segments": self.segments,
            "relations": self.relations,
            "seg_ids": self.seg_ids,
            "retrieval_units": self.retrieval_units,
            "error": self.error,
            "parse_error": self.parse_error,
            "run_document_id": self.run_document_id,
            "sequence_id": self.sequence_id,
            "action": self.action,
            "existing_doc": self.existing_doc,
            "embeddings": self.embeddings,
            "document_id": self.document_id,
            "snapshot_id": self.snapshot_id,
        }
        current.update(kwargs)
        return DocumentContext(**current)


# ---------------------------------------------------------------------------
# Operator Protocols
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pipeline configuration
# ---------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    """Composable pipeline configuration.

    Each field is a pluggable operator. Swap any operator to customize behavior.
    """

    domain: str
    parser_factory: Callable[[str], Any] = field(default=None)
    segmenter: Segmenter | None = None
    enricher: Any | None = None  # Enricher Protocol（篇章本职：语义角色 + 内容质量）
    entity_extractor: Any | None = None  # EntityExtractor（本体线：双通道实体抽取，L4 §15）
    resolver: Any | None = None  # EntityResolver (B3 实体归一 + 人审分流)
    entity_relation_builder: Any | None = None  # EntityRelationBuilder (B4 概念关系抽取)
    question_generator: Any | None = None  # QuestionGenerator Protocol
    embedding_generator: Any | None = None  # EmbeddingGenerator Protocol
    discourse_relation_builder: Any | None = None  # DiscourseRelationBuilder
    contextualizer: Any | None = None  # Contextualizer Protocol
    image_captioner: Any | None = None  # ImageCaptioner (VLM captions for images)
    domain_profile: Any | None = None  # DomainProfile
    fetch_remote_images: bool = False  # Allow http(s) image download at parse

    # DB handles for db_write_stage (set by run.py)
    asset_db: Any | None = None
    runtime_db: Any | None = None
    tracker: Any | None = None
    batch_id: str | None = None
    run_id: str | None = None
    workflow_binding: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Mining pipeline
# ---------------------------------------------------------------------------

class MiningPipeline:
    """Orchestrates per-document processing using pluggable operators."""

    def __init__(self, config: PipelineConfig) -> None:
        self._config = config

    @property
    def config(self) -> PipelineConfig:
        return self._config

    def process_document(
        self,
        ctx: DocumentContext,
        *,
        stage_callback: Any | None = None,
    ) -> DocumentContext:
        """Run all per-document pipeline stages.

        Args:
            ctx: Initial document context (must have raw_file set).
            stage_callback: Optional callback(stage_name, ctx) for tracking.

        Returns:
            Final DocumentContext with all stages populated.
        """
        cfg = self._config

        # Stage 1: Parse
        if stage_callback:
            stage_callback("parse", ctx)
        raw = ctx.raw_file
        if raw is None:
            return ctx
        parser = cfg.parser_factory(raw.file_type) if cfg.parser_factory else None
        if parser is None:
            return ctx
        tree = parser.parse(
            raw.content, raw.file_name, _parse_context(raw, cfg),
        )
        ctx = ctx.with_updates(tree=tree)

        if tree is None:
            return ctx

        # Stage 2: Segment (optional VLM image captions happen inside)
        if stage_callback:
            stage_callback("segment", ctx)
        seg = cfg.segmenter
        if seg is None:
            return ctx
        profile = ctx.profile
        if profile is None:
            return ctx
        tree = _caption_tree_for_segment(tree, cfg)
        segments = seg.segment(tree, profile)
        ctx = ctx.with_updates(tree=tree, segments=tuple(segments))

        # Stage 3: Enrich
        if stage_callback:
            stage_callback("enrich", ctx)
        enricher = cfg.enricher
        if enricher is not None and ctx.segments:
            enriched = enricher.enrich_batch(list(ctx.segments))
            ctx = ctx.with_updates(segments=tuple(enriched))

        # Stage 3a: Entity extract (本体线：双通道实体抽取，独立 LLM 调用)
        extractor = cfg.entity_extractor
        if extractor is not None and ctx.segments:
            if stage_callback:
                stage_callback("entity_extract", ctx)
            extracted = extractor.extract_batch(list(ctx.segments))
            ctx = ctx.with_updates(segments=tuple(extracted))

        # Stage 3b: Resolve (实体归一 + 人审分流)
        resolver = cfg.resolver
        if resolver is not None and ctx.segments:
            if stage_callback:
                stage_callback("resolve", ctx)
            resolved = resolver.resolve_batch(list(ctx.segments))
            ctx = ctx.with_updates(segments=tuple(resolved))

        # Stage 3c: Entity relations (pattern 约束抽概念关系)
        rel_builder = cfg.entity_relation_builder
        if rel_builder is not None and ctx.segments:
            if stage_callback:
                stage_callback("entity_relations", ctx)
            built = rel_builder.build_batch(list(ctx.segments))
            ctx = ctx.with_updates(segments=tuple(built))

        # Stage 4: Assign segment UUIDs
        if ctx.segments:
            from knowledge_mining.mining.stages.relations import build_seg_ids
            ctx = ctx.with_updates(seg_ids=build_seg_ids(list(ctx.segments)))

        # Stage 4b: Build discourse relations (LLM-driven RST analysis)
        drb = cfg.discourse_relation_builder
        if drb is not None and ctx.segments:
            if stage_callback:
                stage_callback("discourse_relations", ctx)
            discourse_relations = drb.build(list(ctx.segments), seg_ids=ctx.seg_ids)
            if discourse_relations:
                ctx = ctx.with_updates(relations=tuple(discourse_relations))

        # Stage 5: Build retrieval units
        if stage_callback:
            stage_callback("build_retrieval_units", ctx)
        if ctx.segments:
            from knowledge_mining.mining.stages.retrieval_units import build_retrieval_units
            units = build_retrieval_units(
                list(ctx.segments),
                seg_ids=ctx.seg_ids,
                document_key=profile.document_key if profile else "",
                question_generator=cfg.question_generator,
                contextualizer=cfg.contextualizer,
                profile=cfg.domain_profile,
            )
            ctx = ctx.with_updates(retrieval_units=tuple(units))

        return ctx


# ---------------------------------------------------------------------------
# Streaming pipeline (queue-based parallel architecture)
# ---------------------------------------------------------------------------

_SENTINEL = object()


def _worker(
    stage_name: str,
    fn: Callable[[DocumentContext], DocumentContext],
    in_q: Queue,
    out_q: Queue,
    run_id: str | None,
    tracker: Any | None,
    summary_fn: Callable[[DocumentContext], str | None] | None = None,
) -> None:
    """Worker thread: pull from in_q, run fn, emit start/end stage events, push to out_q.

    Stage events are emitted only when both run_id and tracker are provided AND
    the ctx carries a run_document_id. This keeps StreamingPipeline usable in
    test contexts where no tracker is wired.

    summary_fn (optional) maps a completed stage's result ctx to a short
    output_summary string (e.g. "segments=12") recorded on the end event — used
    by the run-document detail view to show per-stage产出计数. Failures inside
    summary_fn never break the pipeline (summary falls back to None).
    """
    while True:
        item = in_q.get()
        if item is _SENTINEL:
            break

        rd_id = getattr(item, "run_document_id", None)
        emit = tracker is not None and run_id is not None and rd_id is not None
        # Skip event emission if the document already errored upstream.
        if emit and item.error:
            out_q.put(item)
            continue

        evt_id = None
        if emit:
            try:
                evt_id = tracker.start_stage(run_id, stage_name, rd_id)
            except Exception:
                logger.exception("tracker.start_stage failed for stage=%s", stage_name)
                evt_id = None

        try:
            result = fn(item)
        except Exception as e:
            err_msg = str(e)[:500]
            logger.exception(
                "stage=%s failed for run_document_id=%s: %s",
                stage_name, rd_id, err_msg,
            )
            if evt_id is not None:
                try:
                    tracker.end_stage(evt_id, run_id, stage_name,
                                      status="failed", error_message=err_msg)
                except Exception:
                    logger.exception("tracker.end_stage(failed) failed for stage=%s", stage_name)
            out_q.put(item.with_updates(error=err_msg))
            continue

        if evt_id is not None:
            try:
                summary = None
                if summary_fn is not None:
                    try:
                        summary = summary_fn(result)
                    except Exception:
                        summary = None
                tracker.end_stage(evt_id, run_id, stage_name, output_summary=summary)
            except Exception:
                logger.exception("tracker.end_stage failed for stage=%s", stage_name)
        out_q.put(result)


class StreamingPipeline:
    """Queue-based parallel pipeline. Each stage runs in its own thread(s).

    Usage::

        stages = [
            ("parse",   parse_fn,   1),
            ("enrich",  enrich_fn,  4),  # 4 concurrent workers
            ("publish", publish_fn, 1),
        ]
        pipeline = StreamingPipeline(stages)
        results = pipeline.process_all(items)
    """

    def __init__(
        self,
        stages: list[tuple],
        *,
        run_id: str | None = None,
        tracker: Any | None = None,
    ) -> None:
        """stages 元素为 (name, fn, n_workers) 或可选带第 4 项
        (name, fn, n_workers, summary_fn)，summary_fn 把完成 ctx 映射成 output_summary。"""
        self._stages = stages
        self._queues: list[Queue] = [Queue() for _ in range(len(stages) + 1)]
        self._threads: list[list[Thread]] = []

        for i, spec in enumerate(stages):
            name, fn, n = spec[0], spec[1], spec[2]
            summary_fn = spec[3] if len(spec) > 3 else None
            stage_threads = []
            for w in range(n):
                t = Thread(
                    target=_worker,
                    args=(name, fn, self._queues[i], self._queues[i + 1], run_id, tracker, summary_fn),
                    name=f"mining-{name}-{w}",
                    daemon=True,
                )
                t.start()
                stage_threads.append(t)
            self._threads.append(stage_threads)

    def process_all(self, items: list[DocumentContext]) -> list[DocumentContext]:
        """Submit all items, wait for completion, return results in input order."""
        n = len(items)
        for i, item in enumerate(items):
            self._queues[0].put(item.with_updates(sequence_id=i))

        # Send sentinels stage-by-stage to shut down workers
        for i, stage_threads in enumerate(self._threads):
            for _ in stage_threads:
                self._queues[i].put(_SENTINEL)
            for t in stage_threads:
                t.join()

        results: list[DocumentContext] = []
        while len(results) < n:
            results.append(self._queues[-1].get())
        results.sort(key=lambda ctx: ctx.sequence_id)
        return results


# ---------------------------------------------------------------------------
# Stage functions for StreamingPipeline (closures bind PipelineConfig)
# ---------------------------------------------------------------------------

def parse_stage(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    options: ParseSegmentOptions | None = None,
) -> DocumentContext:
    """Stage 1: Parse raw file into SectionNode tree."""
    raw = ctx.raw_file
    if raw is None:
        return ctx
    parser = cfg.parser_factory(raw.file_type) if cfg.parser_factory else None
    if parser is None:
        return ctx
    parse_ctx = _parse_context(raw, cfg)
    if options is not None and options.fetch_remote_images:
        parse_ctx["fetch_remote_images"] = True
    tree = parser.parse(raw.content, raw.file_name, parse_ctx)
    if tree is None:
        # 解析器软失败：把它记下的原因带出来（PdfParser / DocxParser 才有）。
        return ctx.with_updates(parse_error=getattr(parser, "last_error", None))
    return ctx.with_updates(tree=tree)


def _caption_tree_for_segment(
    tree: SectionNode,
    cfg: PipelineConfig,
    *,
    options: ParseSegmentOptions | None = None,
) -> SectionNode:
    """Optional VLM captions for image blocks; runs inside segment_stage."""
    captioner = cfg.image_captioner
    if captioner is None:
        return tree

    enabled = getattr(captioner, "enabled", True)
    model = getattr(captioner, "model", "glm-4.5v")
    max_images = getattr(captioner, "max_images", 20)
    if options is not None:
        enabled = options.enable_image_caption
        model = options.image_caption_model or model
        max_images = options.max_images_per_doc
    if not enabled:
        return tree

    prev = (captioner.enabled, captioner.model, captioner.max_images)
    captioner.enabled = True
    captioner.model = model
    captioner.max_images = max_images
    try:
        return captioner.caption_tree(tree)
    except Exception as exc:
        logger.warning("image caption during segment failed: %s", exc)
        return tree
    finally:
        captioner.enabled, captioner.model, captioner.max_images = prev


def segment_stage(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    options: ParseSegmentOptions | None = None,
) -> DocumentContext:
    """Stage 2: optional image captions + segment tree + assign stable seg UUIDs.

    Image VLM captioning is part of this stage (not a separate pipeline stage):
    empty image blocks get text filled before the segmenter runs so hashes /
    raw_text see the caption.

    seg_ids are computed here (not in a separate stage) because the work is
    trivial in-memory work and the DB CHECK constraint on stage_events does
    not allocate a slot for a standalone 'seg_ids' stage.
    """
    seg = cfg.segmenter
    if seg is None or ctx.tree is None or ctx.profile is None:
        return ctx
    from knowledge_mining.mining.stages.segment import SegmentPolicyOverride

    tree = _caption_tree_for_segment(ctx.tree, cfg, options=options)
    try:
        from knowledge_mining.mining.infra.image_assets import summarize_image_blocks

        img_stats = summarize_image_blocks(tree)
        if img_stats.get("images"):
            logger.info(
                "segment image stats doc=%s %s",
                getattr(ctx.profile, "document_key", None),
                img_stats,
            )
    except Exception:
        pass

    profile_policy = getattr(cfg.domain_profile, "retrieval_policy", None)
    if options is None:
        policy_override = SegmentPolicyOverride(
            min_segment_tokens=(
                profile_policy.min_chunk_tokens if profile_policy else 128
            ),
            max_segment_tokens=(
                profile_policy.max_chunk_tokens if profile_policy else 512
            ),
            structural_context_mode=(
                profile_policy.structural_context_mode
                if profile_policy else "breadcrumb"
            ),
            merge_small_segments=True,
            absorb_child_orphans=(
                profile_policy.cross_section_merge if profile_policy else True
            ),
            merge_lead_into_child=(
                profile_policy.cross_section_merge if profile_policy else True
            ),
            # Preserve the legacy within-section thresholds.
            merge_small_min_tokens=100,
            merge_small_max_tokens=512,
        )
    else:
        policy_override = SegmentPolicyOverride(
            min_segment_tokens=options.min_segment_tokens,
            max_segment_tokens=options.max_segment_tokens,
            structural_context_mode=options.structural_context_mode,
            merge_small_segments=options.merge_small_segments,
            absorb_child_orphans=options.absorb_child_orphans,
            merge_lead_into_child=options.merge_lead_into_child,
        )
    segments = seg.segment(
        tree,
        ctx.profile,
        domain_profile=cfg.domain_profile,
        policy_override=policy_override,
    )
    updates: dict[str, Any] = {"segments": tuple(segments)}
    if tree is not ctx.tree:
        updates["tree"] = tree
    if not segments:
        return ctx.with_updates(**updates)
    from knowledge_mining.mining.stages.relations import build_seg_ids
    return ctx.with_updates(
        **updates,
        seg_ids=build_seg_ids(list(segments)),
    )


def enrich_stage(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    options: EnrichOptions | None = None,
) -> DocumentContext:
    """Stage 3: Enrich segments (LLM or rule-based)."""
    enricher = cfg.enricher
    if enricher is None or not ctx.segments:
        return ctx
    if options is None:
        enriched = enricher.enrich_batch(list(ctx.segments))
        return ctx.with_updates(segments=tuple(enriched))
    substantial = [
        item
        for item in ctx.segments
        if (item.token_count or 0) >= options.min_enrich_tokens
    ]
    if not substantial:
        return ctx
    enriched_by_index = {
        item.segment_index: item
        for item in enricher.enrich_batch(substantial)
    }
    return ctx.with_updates(segments=tuple(
        enriched_by_index.get(item.segment_index, item) for item in ctx.segments
    ))


def entity_extract_stage(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    options: EntityExtractOptions | None = None,
) -> DocumentContext:
    """Stage 3a: 本体线实体抽取（双通道，独立 LLM 调用，L4 §15）。"""
    extractor = cfg.entity_extractor
    if extractor is None or not ctx.segments:
        return ctx
    kwargs = {}
    if options is not None:
        kwargs = {
            "min_confidence": options.min_confidence,
            "allow_out_of_schema": options.allow_out_of_schema,
            "max_entities_per_segment": options.max_entities_per_segment,
        }
    extracted = extractor.extract_batch(list(ctx.segments), **kwargs)
    return ctx.with_updates(segments=tuple(extracted))


def resolve_stage(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    options: EntityResolveOptions | None = None,
) -> DocumentContext:
    """Stage 3b: 实体归一 + 人审分流（给每个 mention 打 canonical_name + resolve_status）。"""
    resolver = cfg.resolver
    if resolver is None or not ctx.segments:
        return ctx
    if options is not None and not options.auto_resolve_aliases:
        pending = []
        for segment in ctx.segments:
            refs = []
            for ref in segment.entity_refs_json:
                copied = dict(ref)
                copied["canonical_name"] = None
                copied["resolve_status"] = "pending"
                refs.append(copied)
            pending.append(replace(segment, entity_refs_json=refs))
        return ctx.with_updates(segments=tuple(pending))
    resolved = resolver.resolve_batch(list(ctx.segments))
    return ctx.with_updates(segments=tuple(resolved))


def entity_relations_stage(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    options: Any | None = None,
) -> DocumentContext:
    """Stage 3c: 概念关系抽取（pattern 约束，产候选边写进 segment metadata）。"""
    del options
    builder = cfg.entity_relation_builder
    if builder is None or not ctx.segments:
        return ctx
    built = builder.build_batch(list(ctx.segments))
    return ctx.with_updates(segments=tuple(built))


def discourse_stage(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    options: DiscourseOptions | None = None,
) -> DocumentContext:
    """Stage 4b: Build discourse relations (LLM-driven RST analysis)."""
    drb = cfg.discourse_relation_builder
    if drb is None or not ctx.segments:
        return ctx
    kwargs: dict[str, Any] = {"seg_ids": ctx.seg_ids}
    if options is not None:
        kwargs.update(
            window_size=options.window_size,
            min_confidence=options.min_confidence,
        )
    discourse_relations = drb.build(list(ctx.segments), **kwargs)
    if discourse_relations:
        return ctx.with_updates(relations=tuple(discourse_relations))
    return ctx


def contextual_retrieval_stage(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    options: Any | None = None,
) -> DocumentContext:
    """Generate contextual descriptions once and freeze them on segment metadata."""
    del options
    contextualizer = cfg.contextualizer
    if contextualizer is None or not ctx.segments:
        return ctx
    document_text = "\n".join(item.raw_text for item in ctx.segments)
    try:
        context_map = contextualizer.contextualize(
            [item for item in ctx.segments if item.raw_text.strip()], document_text
        )
    except Exception as exc:
        logger.warning("Contextualization failed: %s", exc)
        return ctx
    task_ids = (
        contextualizer.last_task_ids
        if hasattr(contextualizer, "last_task_ids")
        else {}
    )
    enriched = []
    for segment in ctx.segments:
        segment_key = f"{segment.document_key}#{segment.segment_index}"
        context = context_map.get(segment_key)
        task_id = task_ids.get(segment_key)
        if not context and not task_id:
            enriched.append(segment)
            continue
        metadata = dict(segment.metadata_json)
        if context:
            metadata["context_description"] = context
        if task_id:
            metadata["context_task_id"] = task_id
        enriched.append(replace(segment, metadata_json=metadata))
    return ctx.with_updates(segments=tuple(enriched))


def retrieval_units_stage(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    options: RetrievalUnitOptions | None = None,
) -> DocumentContext:
    """Stage 5: Build retrieval units."""
    if not ctx.segments:
        return ctx
    from knowledge_mining.mining.stages.retrieval_units import build_retrieval_units
    profile = ctx.profile
    kwargs: dict[str, Any] = {}
    if options is not None:
        kwargs = {
            "raw_text_unit": options.raw_text_unit,
            "generated_question_unit": options.generated_question_unit,
            "table_row_unit": options.table_row_unit,
            "max_questions_per_segment": options.max_questions_per_segment,
            "min_questionworthy_tokens": options.min_questionworthy_tokens,
            "apply_contextualizer": False,
        }
    units = build_retrieval_units(
        list(ctx.segments),
        seg_ids=ctx.seg_ids,
        document_key=profile.document_key if profile else "",
        question_generator=cfg.question_generator,
        contextualizer=cfg.contextualizer,
        profile=cfg.domain_profile,
        **kwargs,
    )
    return ctx.with_updates(retrieval_units=tuple(units))


def embedding_stage(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    options: EmbeddingOptions | None = None,
) -> DocumentContext:
    """Stage 6: Generate embeddings for retrieval units (parallel HTTP to embedding API).

    Designed to run with multiple workers — each invocation is stateless and only
    makes HTTP calls to the embedding service.
    """
    gen = cfg.embedding_generator
    if gen is None or not ctx.retrieval_units:
        return ctx
    try:
        allowed_types = set(options.unit_types) if options is not None else None
        selected = [
            unit
            for unit in ctx.retrieval_units
            if unit.text and (allowed_types is None or unit.unit_type in allowed_types)
        ]
        texts = [ru.text for ru in selected]
        keys = [ru.unit_key for ru in selected]
        if not texts:
            return ctx
        embeddings = gen.embed_batch(texts)
        if embeddings and len(embeddings) == len(texts):
            emb_data = tuple(
                {"unit_key": k, "vector": v}
                for k, v in zip(keys, embeddings)
                if v
            )
            return ctx.with_updates(embeddings=emb_data)
    except Exception as e:
        logger.warning("Embedding generation failed for %s: %s",
                        getattr(ctx.raw_file, "file_path", "?"), e)
    return ctx


#: 有真正解析器的 file_type。其余一律落到 PassthroughParser（恒返回 None）。
_PARSEABLE_FILE_TYPES = frozenset({"markdown", "txt", "pdf", "docx"})

#: 这些类型的 content 天然为空（解析器直接读 file_path），不能据此判"空文件"。
_FILE_BACKED_TYPES = frozenset({"docx"})


def _classify_parse_skip(ctx: DocumentContext) -> tuple[str, str | None]:
    """把"解析没产出树"细分成可区分的跳过原因码 + 明细。

    顺序有讲究：预处理失败会同时导致 content 为空，若先判空文件就会把"系统没能
    处理它"误报成"这文件本来就没内容"——这两类对用户的意义完全相反。
    """
    raw = ctx.raw_file
    if raw is None:
        return "parse_no_tree", None

    meta = raw.metadata_json if isinstance(raw.metadata_json, dict) else {}
    if meta.get("preprocess_status") == "failed":
        return "preprocess_failed", str(
            meta.get("preprocess_error") or "preprocessing failed"
        )
    pre_err = meta.get("preprocess_error")
    if pre_err:
        return "preprocess_failed", str(pre_err)

    if ctx.parse_error:
        return "parser_failed", ctx.parse_error

    if raw.file_type not in _PARSEABLE_FILE_TYPES:
        return "unsupported_type", raw.file_type

    if raw.file_type not in _FILE_BACKED_TYPES and not (raw.content or "").strip():
        return "empty_file", None

    return "parse_no_tree", None


def _insert_document_embedding(
    asset_db: Any, embedding: dict[str, Any], unit_ids: dict[str, str]
) -> None:
    import json as _json

    unit_key = embedding["unit_key"]
    vector = embedding["vector"]
    if unit_key not in unit_ids or not vector:
        return
    asset_db.insert_retrieval_embedding(
        embedding_id=uuid.uuid4().hex,
        retrieval_unit_id=unit_ids[unit_key],
        embedding_model="managed",
        embedding_provider="llm_service",
        text_kind="full",
        embedding_dim=len(vector),
        embedding_vector=_json.dumps(vector),
        content_hash="",
    )


def persist_document_assets(
    ctx: DocumentContext,
    cfg: PipelineConfig,
    *,
    strict_embeddings: bool,
    graph_store: Any | None = None,
    ontology_store: Any | None = None,
    run_id: str | None = None,
    node_id: str = "asset_persist",
) -> DocumentContext:
    """Persist one document through one Domain-database transaction.

    Workflow mode passes ``strict_embeddings=True`` and the graph stores, making
    snapshot/link, content, retrieval, embeddings, mentions, candidates, and the
    committed document marker one atomic boundary.  Legacy callers keep their
    historical best-effort embedding behavior by passing ``False``.
    """
    if ctx.error:
        raise ValueError(ctx.error)
    if ctx.tree is None and not ctx.snapshot_id:
        # v2 链（segment_compile 已产出快照）不携带旧 parse tree——
        # 解析树在快照的 Parse IR 里，走下方 snapshot_id 分支。
        raise ValueError("document has no parse tree")
    if not ctx.segments:
        raise ValueError("document has no segments")
    if cfg.asset_db is None or ctx.raw_file is None or ctx.profile is None:
        raise RuntimeError("document persistence is not configured")

    asset_db = cfg.asset_db
    unit_ids: dict[str, str] = {}
    segment_ids = dict(ctx.seg_ids)
    output = ctx
    with asset_db.transaction():
        if ctx.snapshot_id:
            document_id = ctx.document_id or (ctx.existing_doc or {}).get("id")
            if not document_id:
                raise RuntimeError(
                    "existing document snapshot has no owning document identity"
                )
            snapshot_id = ctx.snapshot_id
        else:
            document_id, snapshot_id, _ = select_or_create_snapshot(
                asset_db,
                ctx.raw_file,
                ctx.profile,
                domain=cfg.domain,
                batch_id=cfg.batch_id,
                workflow_binding=cfg.workflow_binding,
                existing_document_id=(ctx.existing_doc or {}).get("id"),
            )
        if asset_db.count_segments_by_snapshot(snapshot_id) == 0:
            for segment in ctx.segments:
                segment_key = f"{segment.document_key}#{segment.segment_index}"
                segment_id = segment_ids.get(segment_key, uuid.uuid4().hex)
                segment_ids[segment_key] = segment_id
                asset_db.insert_raw_segment(
                    segment_id=segment_id,
                    document_snapshot_id=snapshot_id,
                    segment_key=segment_key,
                    segment_index=segment.segment_index,
                    block_type=segment.block_type,
                    semantic_role=segment.semantic_role,
                    section_path=segment.section_path,
                    section_title=segment.section_title,
                    raw_text=segment.raw_text,
                    normalized_text=segment.normalized_text,
                    content_hash=segment.content_hash,
                    normalized_hash=segment.normalized_hash,
                    token_count=segment.token_count,
                    structure_json=segment.structure_json,
                    source_offsets_json=segment.source_offsets_json,
                    entity_refs_json=segment.entity_refs_json,
                    metadata_json=segment.metadata_json,
                )
        else:
            # v2 链（segment_compile 预写切片）或重跑：切片归切片——
            # 只回读 id 映射，绝不因此跳过下方 units/relations 的独立栅栏。
            persisted_segments = asset_db.get_segments_by_snapshot(snapshot_id)
            segment_ids = {
                str(item["segment_key"]): str(item["id"])
                for item in persisted_segments
            }

        # 批次4 修复：检索单元不再挂在「快照无切片」栅栏下。老逻辑在
        # segment_compile 预写切片后恒走 else 分支，units/embeddings/relations
        # 整批静默丢弃（全量基线挖掘「成功」但 asset_retrieval_units 恒 0）。
        # 现按「本快照是否已有检索单元」独立幂等判断：首跑必插，重跑不重插。
        units_inserted = not asset_db.get_retrieval_units_by_snapshot(snapshot_id)
        if units_inserted:
            for relation in ctx.relations:
                source_id = segment_ids.get(relation.source_segment_key, "")
                target_id = segment_ids.get(relation.target_segment_key, "")
                if source_id and target_id:
                    asset_db.insert_segment_relation(
                        relation_id=uuid.uuid4().hex,
                        document_snapshot_id=snapshot_id,
                        source_segment_id=source_id,
                        target_segment_id=target_id,
                        relation_type=relation.relation_type,
                        weight=relation.weight,
                        confidence=relation.confidence,
                        distance=relation.distance,
                        metadata_json=relation.metadata_json,
                    )
            for unit in ctx.retrieval_units:
                unit_id = uuid.uuid4().hex
                unit_ids[unit.unit_key] = unit_id
                asset_db.insert_retrieval_unit(
                    unit_id=unit_id,
                    document_snapshot_id=snapshot_id,
                    unit_key=unit.unit_key,
                    unit_type=unit.unit_type,
                    target_type=unit.target_type,
                    target_ref_json=unit.target_ref_json,
                    title=unit.title,
                    text=unit.text,
                    search_text=unit.search_text,
                    block_type=unit.block_type,
                    semantic_role=unit.semantic_role,
                    facets_json=unit.facets_json,
                    entity_refs_json=unit.entity_refs_json,
                    source_refs_json=unit.source_refs_json,
                    llm_result_refs_json=unit.llm_result_refs_json,
                    source_segment_id=unit.source_segment_id,
                    weight=unit.weight,
                    metadata_json=unit.metadata_json,
                )
            if strict_embeddings:
                for embedding in ctx.embeddings:
                    _insert_document_embedding(asset_db, embedding, unit_ids)

        output = ctx.with_updates(
            document_id=document_id,
            snapshot_id=snapshot_id,
            seg_ids=segment_ids,
        )
        if graph_store is not None and ontology_store is not None:
            from contextlib import ExitStack

            from knowledge_mining.mining.stages.graph_write import (
                aggregate_build,
                persist_document_mentions,
            )

            graph = aggregate_build([output], domain_id=cfg.domain)
            with ExitStack() as participants:
                for store in (graph_store, ontology_store):
                    join = getattr(store, "join_transaction", None)
                    if join is not None:
                        participants.enter_context(join(asset_db))
                persist_document_mentions(
                    graph_store,
                    ontology_store,
                    graph,
                    domain_id=cfg.domain,
                    run_id=run_id or "",
                    node_id=node_id,
                    run_document_id=ctx.run_document_id or "",
                )
        if cfg.tracker is not None and ctx.run_document_id:
            runtime_join = getattr(cfg.runtime_db, "join_transaction", None)
            if runtime_join is None:
                cfg.tracker.commit_document(
                    ctx.run_document_id, document_id, snapshot_id
                )
            else:
                with runtime_join(asset_db):
                    cfg.tracker.commit_document(
                        ctx.run_document_id, document_id, snapshot_id
                    )
                    cfg.runtime_db.commit()

    if not strict_embeddings and ctx.embeddings and unit_ids:
        try:
            for embedding in ctx.embeddings:
                _insert_document_embedding(asset_db, embedding, unit_ids)
        except Exception as exc:
            logger.warning(
                "Embedding insert failed for %s (document still committed): %s",
                getattr(ctx.raw_file, "file_path", "?"),
                exc,
            )
    return output


def db_write_stage(ctx: DocumentContext, cfg: PipelineConfig) -> DocumentContext:
    """Legacy Stage 7 wrapper preserving skip and best-effort semantics."""
    tracker = cfg.tracker
    rd_id = ctx.run_document_id
    if ctx.error:
        if tracker and rd_id:
            tracker.fail_document(rd_id, ctx.error)
            try:
                cfg.runtime_db.commit()
            except Exception:
                pass
        return ctx
    if ctx.tree is None:
        reason, detail = _classify_parse_skip(ctx)
        if reason == "preprocess_failed":
            error = detail or reason
            if tracker and rd_id:
                tracker.fail_document(rd_id, error)
                try:
                    cfg.runtime_db.commit()
                except Exception:
                    pass
            return ctx.with_updates(error=error)
        if tracker and rd_id:
            tracker.skip_document(rd_id, reason=reason, detail=detail)
            try:
                cfg.runtime_db.commit()
            except Exception:
                pass
        return ctx
    if not ctx.segments:
        if tracker and rd_id:
            tracker.skip_document(rd_id, reason="no_segments")
            try:
                cfg.runtime_db.commit()
            except Exception:
                pass
        return ctx
    try:
        return persist_document_assets(ctx, cfg, strict_embeddings=False)
    except Exception as exc:
        error = str(exc)[:500]
        logger.exception(
            "db_write_stage failed for %s: %s",
            getattr(ctx.raw_file, "file_path", "?"),
            error,
        )
        if tracker and rd_id:
            try:
                tracker.fail_document(rd_id, error)
                cfg.runtime_db.commit()
            except Exception:
                logger.exception("tracker.fail_document also failed")
        return ctx.with_updates(error=error)
