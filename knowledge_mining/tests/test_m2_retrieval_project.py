"""M2 契约（批次8，24 号 §5.4）：retrieval_unit_project 搜索投影.

- 统一 ``RetrievalRepresentation`` 契约（类型矩阵/canonical/eligibility/
  structural_context/facets/provenance）；
- 投影是**纯函数**：不调 LLM、不写资产库；表示本体落 RepresentationStore
  暂存（asset_persist/M5 才正式入库），bundle 只带计数；
- 同源规则：table whole 与 table_row 是不同 canonical target，row 保留
  table container ref；heading 默认不单独形成正文表示；
- 算子注册：catalog 7 算子，embedding 依赖恢复（retrieval_unit_project
  → embedding）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge_mining.mining.contracts.models import DocumentProfile
from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentElementLink,
)
from knowledge_mining.mining.pipeline import DocumentContext


def _runtime(**services):
    return SimpleNamespace(services=SimpleNamespace(**services))


def _state(context):
    return SimpleNamespace(
        run_document_id="rd-1", doc_key="manual.md", context=context,
        capabilities=frozenset(), tags=(),
        with_context=lambda ctx, capabilities=frozenset(): SimpleNamespace(
            run_document_id="rd-1", doc_key="manual.md", context=ctx,
            capabilities=capabilities, tags=(),
        ),
    )


def _segments() -> tuple[CompiledSegment, ...]:
    return (
        CompiledSegment(
            segment_index=0, block_type="paragraph", raw_text="设备巡检说明正文。",
            heading_chain=((1, "运维手册"),),
            element_ids=("e0",), links=(SegmentElementLink(element_id="e0"),),
            token_count=100,
        ),
        CompiledSegment(
            segment_index=1, block_type="table_row", raw_text="A-101\t风扇停转",
            heading_chain=((1, "运维手册"), (2, "告警表")),
            element_ids=("t0",), links=(SegmentElementLink(element_id="t0"),),
            metadata={"table_header": ["告警码", "原因"], "table_ref": "tbl-1"},
            token_count=30,
        ),
        CompiledSegment(
            segment_index=2, block_type="code", raw_text="systemctl restart x",
            heading_chain=((1, "运维手册"),),
            element_ids=("c0",), links=(SegmentElementLink(element_id="c0"),),
            token_count=15,
        ),
    )


# ---------------------------------------------------------------------------
# 契约与纯投影
# ---------------------------------------------------------------------------


def test_representation_contract_fields_and_types() -> None:
    from knowledge_mining.mining.contracts.retrieval_projection import (
        REPRESENTATION_TYPES,
        RetrievalRepresentation,
    )

    assert "prose" in REPRESENTATION_TYPES
    assert {"query_alias", "summary_alias"} <= set(REPRESENTATION_TYPES)
    rep = RetrievalRepresentation(
        representation_id="manual.md:snap:prose:0",
        representation_type="prose",
        content_type="paragraph",
        content_text="正文",
        target_type="segment",
        target_ref="manual.md#seg:0",
        canonical_evidence_id="manual.md#seg:0",
    )
    assert rep.lexical_eligible is True
    assert rep.dense_eligible is True
    assert rep.returnable is True
    assert rep.provenance["projector"] == "retrieval_unit_project"
    with pytest.raises(Exception):
        rep.content_text = "tampered"  # type: ignore[misc]


def test_project_segments_yields_typed_matrix() -> None:
    from knowledge_mining.mining.retrieval_projection.projector import (
        project_representations,
    )

    reps = project_representations(
        _segments(), document_ref="manual.md", snapshot_ref="snap_1",
    )
    by_type: dict[str, list] = {}
    for rep in reps:
        by_type.setdefault(rep.representation_type, []).append(rep)

    # 类型矩阵：paragraph→prose；table_row→table_row；code→code_block
    assert [r.content_type for r in by_type["prose"]] == ["paragraph"]
    assert len(by_type["table_row"]) == 1
    assert len(by_type["code_block"]) == 1

    # prose：面包屑 structural_context + facets
    prose = by_type["prose"][0]
    assert "运维手册" in prose.structural_context
    assert prose.facets["content_type"] == "paragraph"
    assert prose.facets["document"] == "manual.md"
    assert prose.ordinal == 0

    # table_row：表头进 structural_context 与 content_text，container_ref 指向表
    row = by_type["table_row"][0]
    assert "告警码" in row.structural_context and "原因" in row.structural_context
    assert "A-101" in row.content_text and "风扇停转" in row.content_text
    assert row.target_type == "table_row"
    assert row.container_ref == "tbl-1"

    # code_block：完整代码 + 章节路径
    code = by_type["code_block"][0]
    assert code.content_text == "systemctl restart x"
    assert "运维手册" in code.structural_context


def test_same_source_canonical_rules() -> None:
    """table whole 与 row 是不同 canonical target；同型多行各自成 canonical."""
    from knowledge_mining.mining.retrieval_projection.projector import (
        project_representations,
    )

    segments = (
        CompiledSegment(
            segment_index=0, block_type="table", raw_text="整表文本",
            heading_chain=((1, "章"),),
            metadata={"table_header": ["A", "B"], "table_ref": "tbl-9"},
            token_count=40,
        ),
        CompiledSegment(
            segment_index=1, block_type="table_row", raw_text="v1\tv2",
            heading_chain=((1, "章"),),
            metadata={"table_header": ["A", "B"], "table_ref": "tbl-9",
                      "row_index": 0},
            token_count=10,
        ),
    )
    reps = project_representations(
        segments, document_ref="d.md", snapshot_ref="s1",
    )
    table = next(r for r in reps if r.representation_type == "table")
    row = next(r for r in reps if r.representation_type == "table_row")
    assert table.canonical_evidence_id != row.canonical_evidence_id
    assert table.target_type == "table" and row.target_type == "table_row"
    assert row.container_ref == "tbl-9"
    # 全部表示 id 确定性且不重复
    ids = [r.representation_id for r in reps]
    assert len(ids) == len(set(ids))


def test_heading_does_not_become_standalone_representation() -> None:
    from knowledge_mining.mining.retrieval_projection.projector import (
        project_representations,
    )

    segments = (
        CompiledSegment(
            segment_index=0, block_type="heading", raw_text="第一章",
            heading_chain=((1, "第一章"),),
            token_count=5,
        ),
    )
    reps = project_representations(
        segments, document_ref="d.md", snapshot_ref="s1",
    )
    # heading/navigation 默认不单独形成正文表示；文档级表示（§5.4 矩阵，
    # 标题恰好取自首个 heading）是唯一产物。
    assert [r.representation_type for r in reps] == ["document"]
    assert reps[0].content_text == "第一章"
    assert reps[0].target_ref == "d.md#document"


def test_section_representation_aggregates_direct_children_bounded() -> None:
    from knowledge_mining.mining.retrieval_projection.projector import (
        project_representations,
    )

    segments = (
        CompiledSegment(
            segment_index=0, block_type="paragraph", raw_text="段落一。",
            heading_chain=((1, "第一章"),), token_count=50,
        ),
        CompiledSegment(
            segment_index=1, block_type="paragraph", raw_text="段落二。",
            heading_chain=((1, "第一章"),), token_count=50,
        ),
    )
    reps = project_representations(
        segments, document_ref="d.md", snapshot_ref="s1", include_sections=True,
    )
    section = next(r for r in reps if r.representation_type == "section")
    assert "段落一" in section.content_text and "段落二" in section.content_text
    assert section.target_type == "section"
    assert section.facets["section_path"] == "第一章"


# ---------------------------------------------------------------------------
# handler / 算子注册 / 依赖恢复
# ---------------------------------------------------------------------------


class _FakeParseService:
    def parse_document(self, raw_file, *, params, domain, run_document_id):
        return SimpleNamespace(
            run_id="parse_1", snapshot_id="snap_1",
            parse_ir_storage_object_id="so_ir", parser_fingerprint="fp",
        )


class _FakeCompileService:
    def __init__(self, segments) -> None:
        self._segments = segments

    def compile_for_snapshot(self, *, snapshot_id, parse_ir_storage_object_id, params):
        return SimpleNamespace(
            snapshot_id=snapshot_id, segment_count=len(self._segments),
            compiler_fingerprint="segc", segments=self._segments,
        )


class _FakeProjectService:
    """retrieval_project 门面替身：纯投影 + 暂存写入."""

    def __init__(self, representations) -> None:
        self._reps = representations

    def project_for_snapshot(self, *, snapshot_id, document_ref, params):
        return SimpleNamespace(
            representations=self._reps,
            representation_count=len(self._reps),
            projector_fingerprint="proj-v1",
        )


def _compiled_bundle():
    from knowledge_mining.mining.workflow.handlers.document import (
        document_parse_handler,
        segment_compile_handler,
    )

    raw = SimpleNamespace(file_type="markdown", document_key="manual.md")
    source = DocumentContext(
        raw_file=raw, profile=DocumentProfile(document_key="manual.md"),
        run_document_id="rd-1",
    )
    rt = _runtime(
        document_parse_service=_FakeParseService(),
        segment_compile_service=_FakeCompileService(_segments()),
    )
    parsed = document_parse_handler(_state(source), {}, rt)
    compiled = segment_compile_handler(_state(parsed.outputs.context), {}, rt)
    return compiled.outputs.context


def test_retrieval_unit_project_handler_updates_bundle_and_stages_reps() -> None:
    from knowledge_mining.mining.retrieval_projection.projector import (
        project_representations,
    )
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.document import (
        retrieval_unit_project_handler,
    )

    reps = project_representations(
        _segments(), document_ref="manual.md", snapshot_ref="snap_1",
    )
    service = _FakeProjectService(reps)
    result = retrieval_unit_project_handler(
        _state(_compiled_bundle()), {"includeSections": True},
        _runtime(retrieval_project_service=service),
    )

    assert result.status.value == "success"
    assert "retrieval_units" in result.capabilities
    bundle = result.outputs.context
    assert isinstance(bundle, MiningDocumentBundle)
    assert bundle.representations_count == len(reps)
    assert "retrieval_units" in bundle.capability_facts
    # 序列化边界：bundle 不带表示本体
    assert not hasattr(bundle, "representations")


def test_retrieval_unit_project_rejects_non_bundle() -> None:
    from knowledge_mining.mining.workflow.handlers.document import (
        retrieval_unit_project_handler,
    )

    legacy = DocumentContext(
        profile=DocumentProfile(document_key="manual.md"), run_document_id="rd-1",
    )
    result = retrieval_unit_project_handler(
        _state(legacy), {}, _runtime(retrieval_project_service=_FakeProjectService(())),
    )
    assert result.status.value == "failed"
    assert result.error_code == "retrieval_unit_project_bad_input"


def test_retrieval_unit_project_without_service_fails() -> None:
    from knowledge_mining.mining.workflow.handlers.document import (
        retrieval_unit_project_handler,
    )

    result = retrieval_unit_project_handler(
        _state(_compiled_bundle()), {}, _runtime(),
    )
    assert result.status.value == "failed"
    assert result.error_code == "retrieval_unit_project_unavailable"


def test_catalog_registers_seventh_operator_and_embedding_dependency() -> None:
    from knowledge_mining.mining.workflow.compiler import DEPENDENCIES
    from knowledge_mining.mining.workflow.handlers.document import (
        DOCUMENT_HANDLERS,
    )
    from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog

    catalog = builtin_catalog()
    assert "retrieval_unit_project" in catalog
    assert set(catalog) == {
        "input_ingest", "document_parse", "segment_compile",
        "retrieval_unit_project", "embedding", "asset_persist",
        "mining_finalize",
        "query_expansion_generate", "hierarchical_summary_generate",  # M3
    }
    assert "retrieval_unit_project" in DOCUMENT_HANDLERS
    assert DEPENDENCIES.get("embedding") == {"retrieval_unit_project"}
    project = catalog["retrieval_unit_project"]
    assert set(project.provides) == {"retrieval_units"}
    assert set(project.requires) == {"parsed_segments"}


def test_project_then_embedding_chain_compiles() -> None:
    """embedding 依赖恢复后：compile→project→embedding 链重新可编译."""
    from knowledge_mining.mining.workflow.compiler import WorkflowCompiler
    from knowledge_mining.mining.workflow.graph import (
        EdgeDef,
        NodeDef,
        OutputDef,
        WorkflowGraph,
    )
    from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog

    types = [
        "input_ingest", "document_parse", "segment_compile",
        "retrieval_unit_project", "embedding", "asset_persist",
        "mining_finalize",
    ]
    edges = [
        EdgeDef("input_ingest", "rawFiles", "document_parse", "rawFiles"),
        EdgeDef("document_parse", "documents", "segment_compile", "documents"),
        EdgeDef("segment_compile", "documents", "retrieval_unit_project", "documents"),
        EdgeDef("retrieval_unit_project", "documents", "embedding", "documents"),
        EdgeDef("embedding", "documents", "asset_persist", "discourseAssets"),
        EdgeDef("segment_compile", "documents", "asset_persist", "documents"),
        EdgeDef("asset_persist", "finalizeInput", "mining_finalize", "finalizeInput"),
    ]
    graph = WorkflowGraph(
        schema_version="2.0",
        nodes=tuple(NodeDef(node_id=t, operator_type=t, params={}) for t in types),
        edges=tuple(edges),
        output=OutputDef("mining_finalize", "result"),
    )
    result = WorkflowCompiler(builtin_catalog()).compile(graph, mode="publish")
    assert result.valid is True, [e.kind for e in result.errors]


def test_compiler_vocabulary_maps_into_type_matrix() -> None:
    """27号审查修复：编译器真实词表（list_item/figure）进入投影矩阵，
    与 PG 往返还原（source_block_type）配合后默认链可产出 list/figure 表示。"""
    from knowledge_mining.mining.retrieval_projection.projector import (
        project_representations,
    )

    segments = (
        CompiledSegment(
            segment_index=1, block_type="list_item", raw_text="先断电",
            token_count=3,
        ),
        CompiledSegment(
            segment_index=2, block_type="figure", raw_text="图1 风扇结构",
            metadata={"figure_caption": "风扇结构图"},
            token_count=4,
        ),
    )
    reps = project_representations(
        segments, document_ref="d.md", snapshot_ref="s1",
    )
    by_type = {r.representation_type for r in reps}
    assert "list_group" in by_type
    assert "figure_caption" in by_type
    fig = next(r for r in reps if r.representation_type == "figure_caption")
    assert "风扇结构图" in fig.structural_context
