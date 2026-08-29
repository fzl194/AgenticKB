from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from knowledge_mining.mining.contracts.models import (
    DocumentProfile,
    RawFileData,
    RawSegmentData,
    RetrievalUnitData,
    SectionNode,
    SegmentRelationData,
)
from knowledge_mining.mining.pipeline import (
    DocumentContext,
    PipelineConfig,
    persist_document_assets,
)
from knowledge_mining.mining.infra.db import _DB
from knowledge_mining.mining.infra.ontology_store import OntologyStore
from knowledge_mining.mining.workflow.core import DocumentState, OperatorStatus
from knowledge_mining.mining.workflow.handlers.persist import asset_persist_handler


class FakeAssetDB:
    def __init__(self, *, fail_embeddings: bool = False) -> None:
        self.fail_embeddings = fail_embeddings
        self.rows = {
            "snapshot": [],
            "segments": [],
            "relations": [],
            "retrieval_units": [],
            "embeddings": [],
            "mentions": [],
            "evidence": [],
            "candidates": [],
        }

    @contextmanager
    def transaction(self):
        before = deepcopy(self.rows)
        try:
            yield
        except Exception:
            self.rows = before
            raise

    def count_segments_by_snapshot(self, snapshot_id):
        return sum(row["snapshot_id"] == snapshot_id for row in self.rows["segments"])

    def get_segments_by_snapshot(self, snapshot_id):
        return [
            row for row in self.rows["segments"]
            if row["snapshot_id"] == snapshot_id
        ]

    def get_retrieval_units_by_snapshot(self, snapshot_id):
        return [
            row for row in self.rows["retrieval_units"]
            if row["document_snapshot_id"] == snapshot_id
        ]

    def insert_raw_segment(self, **kwargs):
        self.rows["segments"].append({
            "snapshot_id": kwargs["document_snapshot_id"],
            **kwargs,
        })

    def insert_segment_relation(self, **kwargs):
        self.rows["relations"].append(kwargs)

    def insert_retrieval_unit(self, **kwargs):
        self.rows["retrieval_units"].append(kwargs)

    def insert_retrieval_embedding(self, **kwargs):
        if self.fail_embeddings:
            raise RuntimeError("embedding insert failed")
        self.rows["embeddings"].append(kwargs)


class FakeGraphStore:
    def __init__(self, rows) -> None:
        self.rows = rows

    def delete_snapshot_artifacts(self, snapshot_id):
        self.rows["mentions"] = [
            row for row in self.rows["mentions"] if row["snapshot_id"] != snapshot_id
        ]
        self.rows["evidence"] = [
            row for row in self.rows["evidence"] if row["snapshot_id"] != snapshot_id
        ]

    def upsert_entity(self, domain_id, **kwargs):
        return f"entity:{kwargs['canonical_name']}"

    def add_evidence(self, domain_id, **kwargs):
        self.rows["evidence"].append({
            "snapshot_id": kwargs["document_snapshot_id"],
            **kwargs,
        })
        return f"evidence:{len(self.rows['evidence'])}"

    def add_mention(self, **kwargs):
        self.rows["mentions"].append({
            "snapshot_id": kwargs["document_snapshot_id"],
            **kwargs,
        })
        return f"mention:{len(self.rows['mentions'])}"


class FakeOntologyStore:
    def __init__(self, rows) -> None:
        self.rows = rows

    def upsert_candidate_evidence(self, domain_id, **kwargs):
        key = (
            kwargs["proposed_name"],
            kwargs["run_id"],
            kwargs["node_id"],
            kwargs["run_document_id"],
        )
        self.rows["candidates"] = [
            row for row in self.rows["candidates"] if row["key"] != key
        ]
        self.rows["candidates"].append({"key": key, **kwargs})
        return "candidate:1"


def complete_context() -> DocumentContext:
    segment = RawSegmentData(
        document_key="doc:/a.md",
        segment_index=0,
        block_type="paragraph",
        raw_text="Pump A is equipment.",
        normalized_text="pump a is equipment.",
        entity_refs_json=[{
            "type": "equipment",
            "name": "Pump A",
            "canonical_name": "Pump A",
            "resolve_status": "auto",
        }],
        metadata_json={
            "relation_candidates": [{
                "head": "Pump A",
                "tail": "Equipment",
                "head_type": "equipment",
                "tail_type": "category",
            }]
        },
    )
    return DocumentContext(
        raw_file=RawFileData(
            file_path="C:/fixture/a.md",
            relative_path="a.md",
            file_name="a.md",
            file_type="markdown",
            content="Pump A is equipment.",
            raw_content_hash="raw",
            normalized_content_hash="norm",
        ),
        profile=DocumentProfile(document_key="doc:/a.md", title="A"),
        tree=SectionNode(title="A", level=1),
        segments=(segment,),
        relations=(SegmentRelationData(
            "doc:/a.md#0", "doc:/a.md#0", "elaborates"
        ),),
        seg_ids={"doc:/a.md#0": "segment-1"},
        retrieval_units=(RetrievalUnitData(
            segment_key="doc:/a.md#0",
            unit_key="ru:1",
            unit_type="raw_text",
            target_type="raw_segment",
            text="Pump A is equipment.",
            search_text="pump a equipment",
        ),),
        embeddings=({"unit_key": "ru:1", "vector": [0.1, 0.2]},),
        run_document_id="doc-1",
    )


def fake_snapshot(
    db, raw, profile, *, domain, batch_id, workflow_binding=None,
    existing_document_id=None,
):
    db.rows["snapshot"].append({"document": "document-1", "snapshot": "snapshot-1"})
    return "document-1", "snapshot-1", "link-1"


def test_strict_embedding_failure_rolls_back_every_document_asset(monkeypatch) -> None:
    asset_db = FakeAssetDB(fail_embeddings=True)
    monkeypatch.setattr(
        "knowledge_mining.mining.pipeline.select_or_create_snapshot", fake_snapshot
    )

    try:
        persist_document_assets(
            complete_context(),
            PipelineConfig(domain="odn", asset_db=asset_db, batch_id="batch-1"),
            strict_embeddings=True,
            graph_store=FakeGraphStore(asset_db.rows),
            ontology_store=FakeOntologyStore(asset_db.rows),
            run_id="run-1",
            node_id="asset-persist",
        )
    except RuntimeError as exc:
        assert str(exc) == "embedding insert failed"
    else:
        raise AssertionError("strict persistence must surface embedding failure")

    assert all(not rows for rows in asset_db.rows.values())


def test_strict_persist_commits_all_asset_families_together(monkeypatch) -> None:
    asset_db = FakeAssetDB()
    monkeypatch.setattr(
        "knowledge_mining.mining.pipeline.select_or_create_snapshot", fake_snapshot
    )

    result = persist_document_assets(
        complete_context(),
        PipelineConfig(domain="odn", asset_db=asset_db, batch_id="batch-1"),
        strict_embeddings=True,
        graph_store=FakeGraphStore(asset_db.rows),
        ontology_store=FakeOntologyStore(asset_db.rows),
        run_id="run-1",
        node_id="asset-persist",
    )

    assert result.document_id == "document-1"
    assert result.snapshot_id == "snapshot-1"
    assert {key for key, rows in asset_db.rows.items() if rows} == {
        "snapshot",
        "segments",
        "relations",
        "retrieval_units",
        "embeddings",
        "mentions",
        "evidence",
        "candidates",
    }


def test_persist_reuses_v2_snapshot_without_creating_a_second_snapshot(
    monkeypatch,
) -> None:
    asset_db = FakeAssetDB()

    def fail_if_snapshot_is_created(*args, **kwargs):
        raise AssertionError("v2 snapshot must be reused, not recreated")

    monkeypatch.setattr(
        "knowledge_mining.mining.pipeline.select_or_create_snapshot",
        fail_if_snapshot_is_created,
    )
    context = complete_context().with_updates(
        document_id="document-existing",
        snapshot_id="snapshot-created-by-document-parse",
    )

    result = persist_document_assets(
        context,
        PipelineConfig(domain="odn", asset_db=asset_db, batch_id="batch-1"),
        strict_embeddings=True,
    )

    assert result.document_id == "document-existing"
    assert result.snapshot_id == "snapshot-created-by-document-parse"
    assert {
        row["snapshot_id"] for row in asset_db.rows["segments"]
    } == {"snapshot-created-by-document-parse"}
    assert {
        row["document_snapshot_id"] for row in asset_db.rows["retrieval_units"]
    } == {"snapshot-created-by-document-parse"}
    assert asset_db.rows["embeddings"][0]["retrieval_unit_id"] == (
        asset_db.rows["retrieval_units"][0]["unit_id"]
    )


def test_v2_pre_written_segments_still_persist_units_relations_embeddings() -> None:
    """批次4 修复回归锁：v2 链 segment_compile 预写切片后，
    persist_document_assets 不得因『快照已有切片』跳过检索单元/关系/向量。

    事故背景：2026-08 全量基线挖掘全部『成功』但 asset_retrieval_units 恒 0 条——
    老栅栏 count_segments==0 把 units/embeddings 插入整批挡在 else 分支外。"""
    asset_db = FakeAssetDB()
    # 模拟 segment_compile：快照已建、切片已预写（真实 PgSegmentStore 行形状）
    asset_db.rows["segments"].append({
        "snapshot_id": "snapshot-v2",
        "segment_key": "doc:/a.md#0",
        "id": "seg-pre-1",
    })
    context = complete_context().with_updates(
        document_id="document-v2",
        snapshot_id="snapshot-v2",
    )

    result = persist_document_assets(
        context,
        PipelineConfig(domain="odn", asset_db=asset_db, batch_id="batch-1"),
        strict_embeddings=True,
    )

    # 切片不重复插（预写行原样保留，seg_ids 复用预写 id）
    assert len(asset_db.rows["segments"]) == 1
    assert asset_db.rows["segments"][0]["id"] == "seg-pre-1"
    assert result.seg_ids == {"doc:/a.md#0": "seg-pre-1"}
    # 检索单元必须落库（修复点）
    assert [
        row["unit_key"] for row in asset_db.rows["retrieval_units"]
    ] == ["ru:1"]
    assert asset_db.rows["retrieval_units"][0]["document_snapshot_id"] == (
        "snapshot-v2"
    )
    # 关系必须落库（同样被老栅栏静默丢弃）
    assert len(asset_db.rows["relations"]) == 1
    # 向量必须落库且挂靠到本次插入的单元
    assert len(asset_db.rows["embeddings"]) == 1
    assert asset_db.rows["embeddings"][0]["retrieval_unit_id"] == (
        asset_db.rows["retrieval_units"][0]["unit_id"]
    )


def test_persist_is_idempotent_when_units_already_persisted() -> None:
    """重跑防护：快照已有检索单元时，不得重复插 units/relations/embeddings。"""
    asset_db = FakeAssetDB()
    asset_db.rows["segments"].append({
        "snapshot_id": "snapshot-v2",
        "segment_key": "doc:/a.md#0",
        "id": "seg-pre-1",
    })
    asset_db.rows["retrieval_units"].append({
        "document_snapshot_id": "snapshot-v2",
        "unit_key": "ru:1",
        "unit_id": "unit-existing",
    })
    asset_db.rows["embeddings"].append({
        "retrieval_unit_id": "unit-existing"
    })
    context = complete_context().with_updates(
        document_id="document-v2",
        snapshot_id="snapshot-v2",
    )

    persist_document_assets(
        context,
        PipelineConfig(domain="odn", asset_db=asset_db, batch_id="batch-1"),
        strict_embeddings=True,
    )

    assert len(asset_db.rows["retrieval_units"]) == 1
    assert len(asset_db.rows["embeddings"]) == 1
    assert len(asset_db.rows["relations"]) == 0


def test_generated_segment_identity_is_reused_by_document_mentions(monkeypatch) -> None:
    asset_db = FakeAssetDB()
    monkeypatch.setattr(
        "knowledge_mining.mining.pipeline.select_or_create_snapshot", fake_snapshot
    )
    context = complete_context().with_updates(seg_ids={})

    result = persist_document_assets(
        context,
        PipelineConfig(domain="odn", asset_db=asset_db),
        strict_embeddings=True,
        graph_store=FakeGraphStore(asset_db.rows),
        ontology_store=FakeOntologyStore(asset_db.rows),
        run_id="run-1",
    )

    inserted_id = asset_db.rows["segments"][0]["segment_id"]
    assert result.seg_ids == {"doc:/a.md#0": inserted_id}
    assert asset_db.rows["mentions"][0]["segment_id"] == inserted_id


class FakeRuntimeRepository:
    def __init__(self) -> None:
        self.committed: dict[str, tuple[str, str]] = {}

    def document_persist_marker(self, run_document_id):
        return self.committed.get(run_document_id)


def test_asset_persist_handler_is_idempotent_after_committed_marker() -> None:
    """M5：bundle + 已提交 marker → 早退 SUCCESS，不再触碰持久化服务."""
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle

    repository = FakeRuntimeRepository()
    repository.committed["doc-1"] = ("document-1", "snapshot-1")
    calls = []

    def _must_not_persist(**kwargs):
        calls.append(kwargs)
        raise AssertionError("marker path must not call persist service")

    runtime = SimpleNamespace(
        domain="odn",
        ontology_version_id="ontology-v1",
        runtime_repository=repository,
        services=SimpleNamespace(
            asset_persist_service=SimpleNamespace(
                persist_for_snapshot=_must_not_persist,
            ),
        ),
        manifest={"runId": "run-1"},
    )
    bundle = MiningDocumentBundle(
        document_ref="doc:/a.md", run_document_id="doc-1", snapshot_ref="s1",
    )
    state = DocumentState("doc-1", "doc:/a.md", bundle)

    first = asset_persist_handler(state, {}, runtime)
    second = asset_persist_handler(state, {}, runtime)

    assert first.status is OperatorStatus.SUCCESS
    assert second.status is OperatorStatus.SUCCESS
    assert calls == []
    assert "assets_persisted" in second.outputs.context.capability_facts



def test_retry_replaces_only_same_document_candidate_evidence() -> None:
    rows = {"candidates": []}
    store = FakeOntologyStore(rows)
    common = {
        "kind": "relation_type",
        "proposed_name": "equipment->category",
        "run_id": "run-1",
        "node_id": "asset-persist",
        "payload": {"cooccur": 1},
        "evidence": {"quote": "Pump A is equipment"},
        "score": 1.0,
    }

    store.upsert_candidate_evidence("odn", run_document_id="doc-1", **common)
    store.upsert_candidate_evidence("odn", run_document_id="doc-2", **common)
    store.upsert_candidate_evidence("odn", run_document_id="doc-1", **common)

    assert [row["run_document_id"] for row in rows["candidates"]] == [
        "doc-2",
        "doc-1",
    ]


def test_domain_adapters_can_join_the_owning_asset_transaction() -> None:
    connection = SimpleNamespace(autocommit=False)

    class Pool:
        @contextmanager
        def connection(self):
            yield connection

    pool = Pool()
    assets = _DB(pool)
    graph = _DB(pool)

    with assets.transaction():
        with graph.join_transaction(assets):
            assert graph._tx_conn.get() is assets._tx_conn.get()
            assert graph._tx_conn.get() is connection

    assert graph._tx_conn.get() is None


def test_candidate_evidence_retry_recomputes_aggregate_without_losing_other_document() -> None:
    store = object.__new__(OntologyStore)
    store._fetchone = MagicMock(return_value={
        "id": "candidate-1",
        "status": "proposed",
        "payload_json": {},
        "evidence_json": [
            {
                "run_id": "run-1",
                "node_id": "asset-persist",
                "run_document_id": "doc-1",
                "payload": {"cooccur": 1, "examples": [["old", "value"]]},
                "evidence": {},
                "score": 1.0,
            },
            {
                "run_id": "run-1",
                "node_id": "asset-persist",
                "run_document_id": "doc-2",
                "payload": {"cooccur": 2, "examples": [["B", "C"]]},
                "evidence": {},
                "score": 2.0,
            },
        ],
    })
    store._execute = MagicMock()

    candidate_id = store.upsert_candidate_evidence(
        "odn",
        kind="relation_type",
        proposed_name="equipment->category",
        run_id="run-1",
        node_id="asset-persist",
        run_document_id="doc-1",
        payload={"cooccur": 4, "examples": [["A", "D"]]},
        evidence={"quote": "A D"},
        score=4.0,
    )

    assert candidate_id == "candidate-1"
    params = store._execute.call_args.args[1]
    aggregate = json.loads(params[0])
    evidence = json.loads(params[1])
    assert aggregate["cooccur"] == 6
    assert aggregate["examples"] == [["B", "C"], ["A", "D"]]
    assert params[2] == 6.0
    assert [item["run_document_id"] for item in evidence] == ["doc-2", "doc-1"]
