"""M5 契约（批次8，24 号 §5.8/§7）：三面资产持久化 + readiness 四能力.

- 结构化面投影：CompiledSegment → structure nodes/edges + typed table
  asset + table cells（表头×行值）；
- readiness 四能力事实：search_ready / structure_navigate_ready /
  structured_query_ready / aggregate_ready（由真实资产推导，非节点 SUCCESS）；
- FTS 分词契约：lexical 文本经 tokenize_for_search（jieba）预分词，
  tokenizer 版本进 provenance；
- AssetPersistService：读三个暂存 store → 三面写入（快照级替换）→
  readiness 事实；asset_persist handler 消费 bundle 正常 SUCCESS。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrievalRepresentation,
)
from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentElementLink,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _segments() -> tuple[CompiledSegment, ...]:
    return (
        CompiledSegment(
            segment_index=0, block_type="paragraph", raw_text="巡检说明正文。",
            heading_chain=((1, "运维手册"), (2, "告警表")),
            element_ids=("e0",), links=(SegmentElementLink(element_id="e0"),),
            token_count=100,
        ),
        CompiledSegment(
            segment_index=1, block_type="table_row", raw_text="A-101\t30",
            heading_chain=((1, "运维手册"), (2, "告警表")),
            element_ids=("t0",), links=(SegmentElementLink(element_id="t0"),),
            metadata={
                "table_header": ["告警码", "功耗"],
                "table_ref": "tbl-1",
                "row_index": 0,
            },
            token_count=30,
        ),
    )


def _reps() -> tuple[RetrievalRepresentation, ...]:
    def rep(i, t, ct, lexical=True, text="正文"):
        return RetrievalRepresentation(
            representation_id=f"d:s1:{t}:{i}",
            representation_type=t,
            content_type=ct,
            content_text=text,
            structural_context="运维手册 > 告警表",
            target_type=t if t != "prose" else "segment",
            target_ref=f"d#{t}:{i}",
            canonical_evidence_id=f"d#{t}:{i}",
            lexical_eligible=lexical,
        )

    return (rep(0, "prose", "paragraph", text="巡检说明正文。"),
            rep(1, "table_row", "table_row", text="告警码为A-101；功耗为30"))


# ---------------------------------------------------------------------------
# 结构化面投影
# ---------------------------------------------------------------------------


def test_structure_projection_builds_nodes_edges_table_cells():
    from knowledge_mining.mining.retrieval_projection.structure_projection import (
        project_structure,
    )

    structure = project_structure(_segments(), document_ref="manual.md")

    node_types = {n["node_type"] for n in structure.nodes}
    assert {"document", "section", "segment"} <= node_types
    # 章节节点来自真实标题链
    titles = [n.get("title") for n in structure.nodes if n["node_type"] == "section"]
    assert "运维手册" in titles and "告警表" in titles
    # 边：parent（告警表→运维手册→document）与 order（segment 间）
    relations = {(e["relation"], e["to_ref"]) for e in structure.edges}
    assert any(r == "parent" for r, _ in relations)
    assert any(r == "order" for r, _ in relations)
    # typed table asset + cells（表头×行值）
    assert structure.table_assets and structure.table_assets[0]["asset_type"] == "table"
    cells = structure.table_cells
    assert ("告警码", "A-101") in {(c["column"], c["value"]) for c in cells}
    assert ("功耗", "30") in {(c["column"], c["value"]) for c in cells}


def test_numeric_column_marks_aggregate_ready():
    from knowledge_mining.mining.retrieval_projection.readiness import (
        column_aggregability,
    )

    cells_numeric = [
        {"column": "功耗", "value": "30", "row": 0},
        {"column": "功耗", "value": "45", "row": 1},
        {"column": "告警码", "value": "A-101", "row": 0},
    ]
    agg = column_aggregability(cells_numeric)
    assert agg["功耗"]["can_aggregate"] is True
    assert agg["告警码"]["can_aggregate"] is False


# ---------------------------------------------------------------------------
# readiness 四能力
# ---------------------------------------------------------------------------


def test_readiness_four_capabilities_from_real_assets():
    from knowledge_mining.mining.retrieval_projection.readiness import (
        compute_readiness,
    )
    from knowledge_mining.mining.retrieval_projection.structure_projection import (
        project_structure,
    )

    structure = project_structure(_segments(), document_ref="manual.md")
    facts = compute_readiness(
        representations=_reps(),
        structure=structure,
        embedding_records=(
            SimpleNamespace(representation_id="d:s1:prose:0", dimension=8),
        ),
    )
    assert facts["search_ready"] is True
    assert facts["structure_navigate_ready"] is True
    assert facts["structured_query_ready"] is True
    assert facts["aggregate_ready"] is True  # 功耗列全数值
    assert facts["dense_ready"] is True

    # 无表格资产：structured_query/aggregate 关闭
    no_table = SimpleNamespace(
        nodes=structure.nodes, edges=structure.edges, table_assets=(), table_cells=(),
    )
    facts2 = compute_readiness(
        representations=_reps(), structure=no_table, embedding_records=(),
    )
    assert facts2["structured_query_ready"] is False
    assert facts2["aggregate_ready"] is False
    assert facts2["dense_ready"] is False
    assert facts2["search_ready"] is True  # lexical 仍可用


def test_readiness_27fix_rules():
    """27号审查修复：单段可导航 / 表头不算结构化数据 / 空 dimension 不计覆盖 /
    readiness 随 faces 落库。"""
    from knowledge_mining.mining.retrieval_projection.readiness import (
        compute_readiness,
    )
    from knowledge_mining.mining.retrieval_projection.structure_projection import (
        project_structure,
    )

    # 1) 单段文档：无 order 边（segment_index>0 才产出）但导航可用
    single = (
        CompiledSegment(
            segment_index=0, block_type="paragraph", raw_text="只有一段。",
            token_count=10,
        ),
    )
    facts = compute_readiness(
        representations=(),
        structure=project_structure(single, document_ref="d.md"),
        embedding_records=(),
    )
    assert facts["structure_navigate_ready"] is True

    # 2) 只有表头没有数据行：structured_query_ready 必须为 False
    header_only = SimpleNamespace(
        nodes=({"node_type": "document", "ref": "d"},),
        edges=(),
        table_assets=(
            {"asset_ref": "d#table:t1", "readiness": "ready",
             "columns": ["型号"]},
        ),
        table_cells=(),
    )
    facts2 = compute_readiness(
        representations=(), structure=header_only, embedding_records=(),
    )
    assert facts2["structured_query_ready"] is False

    # 3) dimension=0 的 embedding 记录（空向量占位）不计入 dense 覆盖
    facts3 = compute_readiness(
        representations=_reps(),
        structure=project_structure(_segments(), document_ref="manual.md"),
        embedding_records=(
            SimpleNamespace(representation_id="d:s1:prose:0", dimension=0),
        ),
    )
    assert facts3["dense_ready"] is False


def test_fts_lexical_text_is_presegmented():
    from knowledge_mining.mining.retrieval_projection.persist import lexical_text

    text = lexical_text("巡检说明正文。", structural_context="运维手册")
    # CJK 被分词器切成空格分隔 token（两侧同源契约：索引侧预分词）
    assert " " in text.strip()
    assert "巡检" in text


# ---------------------------------------------------------------------------
# AssetPersistService + handler
# ---------------------------------------------------------------------------


def _seeded_stores():
    from knowledge_mining.mining.retrieval_projection.embedding import EmbeddingRecord
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryEmbeddingStore,
        MemoryRepresentationStore,
    )
    from knowledge_mining.mining.segment_compiler.repositories_memory import (
        MemorySegmentStore,
    )

    seg_store, rep_store, emb_store = (
        MemorySegmentStore(), MemoryRepresentationStore(), MemoryEmbeddingStore()
    )
    _run(seg_store.replace_for_snapshot("s1", _segments(), "segc", document_key="manual.md"))
    _run(rep_store.replace_for_snapshot("s1", _reps(), "proj", document_key="manual.md"))
    _run(emb_store.replace_for_snapshot(
        "s1",
        (EmbeddingRecord(
            embedding_id="s1:d:s1:prose:0", representation_id="d:s1:prose:0",
            strategy="structural", strategy_input="x", input_hash="h",
            policy_version="emb-policy-1", provider="p", model="m",
            model_version="v", dimension=8, context_group_hash="g",
        ),),
        "emb-policy-1", document_key="manual.md",
    ))
    return seg_store, rep_store, emb_store


def test_persist_service_writes_three_faces_and_readiness():
    from knowledge_mining.mining.retrieval_projection.persist import (
        AssetPersistService,
        MemoryAssetWriter,
    )

    seg_store, rep_store, emb_store = _seeded_stores()
    writer = MemoryAssetWriter()
    service = AssetPersistService(
        segment_store=seg_store,
        representation_store=rep_store,
        embedding_store=emb_store,
        writer=writer,
    )
    outcome = service.persist_for_snapshot(
        snapshot_id="s1", document_ref="manual.md",
    )

    # 三面写入
    assert writer.snapshots["s1"]["raw_segment_count"] == 2
    assert writer.snapshots["s1"]["representation_count"] == 2
    assert writer.snapshots["s1"]["embedding_count"] == 1
    assert writer.snapshots["s1"]["structure_node_count"] >= 3
    # readiness 事实随持久化产出
    assert outcome.readiness["search_ready"] is True
    assert outcome.readiness["structured_query_ready"] is True
    assert outcome.schema_version
    assert outcome.tokenizer_version


def test_asset_persist_handler_succeeds_with_bundle():
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.persist import (
        asset_persist_handler,
    )

    bundle = MiningDocumentBundle(
        document_ref="manual.md", run_document_id="rd-1", snapshot_ref="s1",
        document_id="doc-1", representations_count=2, embeddings_count=1,
    )
    service = SimpleNamespace(
        persist_for_snapshot=lambda *, snapshot_id, document_ref: SimpleNamespace(
            readiness={"search_ready": True}, document_id="doc-1",
            snapshot_id=snapshot_id,
        ),
    )
    repository = SimpleNamespace(document_persist_marker=lambda _rd: None)
    runtime = SimpleNamespace(
        services=SimpleNamespace(asset_persist_service=service),
        runtime_repository=repository,
        manifest={"runId": "run-1"},
    )
    state = SimpleNamespace(
        run_document_id="rd-1", doc_key="manual.md", context=bundle,
        capabilities=frozenset(), tags=(),
        with_context=lambda ctx, capabilities=frozenset(): SimpleNamespace(
            run_document_id="rd-1", doc_key="manual.md", context=ctx,
            capabilities=capabilities, tags=(),
        ),
    )
    result = asset_persist_handler(state, {}, runtime)

    assert result.status.value == "success", result.error_message
    assert "assets_persisted" in result.capabilities
    out = result.outputs.context
    assert isinstance(out, MiningDocumentBundle)
    assert "assets_persisted" in out.capability_facts
    assert out.diagnostics["readiness"]["search_ready"] is True


def test_schema_ddl_contains_v2_tables_and_tsvector():
    from knowledge_mining.mining.retrieval_projection.schema import (
        ASSET_SCHEMA_V2_STATEMENTS,
        TOKENIZER_VERSION,
    )

    joined = "\n".join(ASSET_SCHEMA_V2_STATEMENTS)
    for table in (
        "asset_raw_segments", "asset_structure_nodes", "asset_structure_edges",
        "asset_structured_assets", "asset_table_cells",
        "asset_retrieval_units_v2", "asset_retrieval_embeddings_v2",
    ):
        assert table in joined, table
    assert "tsvector" in joined
    assert TOKENIZER_VERSION


def test_persist_faces_carry_readiness_and_tokenizer():
    """27号审查修复 B：readiness/tokenizer_version 进 faces——PgAssetWriter
    据此原子写 asset_snapshot_readiness，finalize 门禁与 inspect 消费。"""
    from knowledge_mining.mining.retrieval_projection.persist import (
        AssetPersistService,
        MemoryAssetWriter,
    )

    seg_store, rep_store, emb_store = _seeded_stores()
    writer = MemoryAssetWriter()
    service = AssetPersistService(
        segment_store=seg_store,
        representation_store=rep_store,
        embedding_store=emb_store,
        writer=writer,
    )
    service.persist_for_snapshot(snapshot_id="s1", document_ref="manual.md")

    faces = writer.snapshots["s1"]
    assert faces["readiness"]["search_ready"] is True
    assert faces["readiness"]["structured_query_ready"] is True
    assert faces["tokenizer_version"]
