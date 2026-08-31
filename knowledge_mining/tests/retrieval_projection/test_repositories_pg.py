"""三面资产 PG 三件套测试（批次8 M5 生产化）.

两层防线，与 segment_compiler/test_repositories_pg.py 同惯例：

- 无库环境恒跑：SQL 文本/参数化契约 + 接口形状——录制型 pool 捕获每条
  语句与参数，锁定「快照级替换 = 事务内清旧插新」的 SQL 形态、lexical
  匹配注入、向量文本格式与维度校验、embeddings 只补元数据（ON CONFLICT
  DO NOTHING）、raw 面不写（归属 PgSegmentStore）。
- 真库门禁（``KB_RUN_POSTGRES_ACCEPTANCE=1`` + ``*_test`` 库）：round-trip
  + 真约束（tsvector 生成列 / VECTOR(1024) / JSONB）验证。
"""
from __future__ import annotations

import json
import os
from typing import Any

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# 录制型 pool（async 仓储形态；断言只依赖语句文本与参数）
# ---------------------------------------------------------------------------


class _RecordingCursor:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    async def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list:
        return list(self._rows)


class _RecordingTransaction:
    def __init__(self, log: list) -> None:
        self._log = log

    async def __aenter__(self) -> None:
        self._log.append(("<TRANSACTION>", None))

    async def __aexit__(self, *exc: Any) -> None:
        self._log.append(("</TRANSACTION>", None))


class _RecordingConnection:
    def __init__(self, select_rows: list | None = None) -> None:
        self.log: list[tuple[str, list | None]] = []
        self.select_rows = select_rows or []

    async def execute(self, query: str, params: Any = None) -> _RecordingCursor:
        normalized = " ".join(query.split())
        self.log.append((normalized, list(params) if params is not None else None))
        if normalized.startswith("SELECT"):
            return _RecordingCursor(self.select_rows)
        return _RecordingCursor([])

    def transaction(self) -> _RecordingTransaction:
        return _RecordingTransaction(self.log)


class _RecordingConnectionContext:
    def __init__(self, connection: _RecordingConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _RecordingConnection:
        return self._connection

    async def __aexit__(self, *exc: Any) -> None:
        return None


class RecordingPool:
    """异步仓储形态的最小 pool 替身（连接与语句全录制）.

    ``connection()`` 返回 async-with 上下文（对齐 psycopg AsyncConnectionPool
    与 new_chain_services._SyncPoolAsyncAdapter 的消费形态）。
    """

    def __init__(self, select_rows: list | None = None) -> None:
        self.conn = _RecordingConnection(select_rows)
        self.log = self.conn.log

    def connection(self) -> _RecordingConnectionContext:
        return _RecordingConnectionContext(self.conn)


def recording_pool(select_rows: list | None = None) -> RecordingPool:
    return RecordingPool(select_rows)


def statements_of(pool: RecordingPool) -> list[tuple[str, list | None]]:
    return pool.log


def find_statement(
    pool: RecordingPool, fragment: str,
) -> tuple[str, list | None]:
    matches = [entry for entry in statements_of(pool) if fragment in entry[0]]
    assert matches, f"statement not executed: {fragment!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# 构造样本
# ---------------------------------------------------------------------------


def _representation(representation_id: str = "d:s1:prose:0", **overrides: Any):
    from knowledge_mining.mining.contracts.retrieval_projection import (
        RetrievalRepresentation,
    )

    fields: dict[str, Any] = dict(
        representation_id=representation_id,
        representation_type="prose",
        content_type="paragraph",
        content_text="巡检说明正文。",
        structural_context="运维手册 > 告警表",
        target_type="segment",
        target_ref=f"d#seg:{representation_id[-1]}",
        canonical_evidence_id=f"d#ce:{representation_id}",
        container_ref=None,
        ordinal=0,
        lexical_eligible=True,
        dense_eligible=True,
        returnable=True,
        facets={"document": "manual.md"},
        provenance={"projector": "retrieval_unit_project", "projector_version": "1"},
    )
    fields.update(overrides)
    return RetrievalRepresentation(**fields)


def _embedding_record(
    embedding_id: str = "s1:d:s1:prose:0",
    representation_id: str = "d:s1:prose:0",
    dimension: int = 4,
    **overrides: Any,
):
    from knowledge_mining.mining.retrieval_projection.embedding import (
        EmbeddingRecord,
    )

    fields: dict[str, Any] = dict(
        embedding_id=embedding_id,
        representation_id=representation_id,
        strategy="structural",
        strategy_input="面包屑 + 正文",
        input_hash="hash-" + embedding_id,
        policy_version="emb-policy-1",
        provider="test-provider",
        model="embedding-3",
        model_version="v1",
        dimension=dimension,
        context_group_hash="group-hash",
        fallback_from=None,
    )
    fields.update(overrides)
    return EmbeddingRecord(**fields)


def _faces() -> dict[str, Any]:
    """persist.py AssetPersistService 的 faces 形状（最小完备样本）."""
    return {
        "schema_version": "asset-v2-1",
        "persist_version": "1",
        "document_ref": "manual.md",
        "raw_segments": (
            {"segment_index": 0, "block_type": "paragraph",
             "raw_text": "巡检说明正文。", "heading_chain_json": "[]",
             "metadata_json": "{}", "token_count": 100},
        ),
        "structure_nodes": (
            {"node_type": "document", "ref": "manual.md", "title": "manual.md"},
            {"node_type": "section", "ref": "manual.md#section:告警表",
             "title": "告警表", "level": 2, "parent_ref": "manual.md"},
            {"node_type": "segment", "ref": "manual.md#seg:0",
             "parent_ref": "manual.md#section:告警表", "ordinal": 0,
             "block_type": "paragraph"},
        ),
        "structure_edges": (
            {"relation": "parent", "from_ref": "manual.md#seg:0",
             "to_ref": "manual.md#section:告警表"},
        ),
        "table_assets": (
            {"asset_type": "table", "asset_ref": "manual.md#table:tbl-1",
             "table_ref": "tbl-1", "columns": ["告警码", "功耗"],
             "row_count": 1, "readiness": "ready"},
        ),
        "table_cells": (
            {"table_ref": "tbl-1", "row": 0, "column_index": 0,
             "column": "告警码", "value": "A-101", "is_header": False},
        ),
        "representations": (
            {"representation_id": "d:s1:prose:0", "representation_type": "prose",
             "content_type": "paragraph", "content_text": "巡检说明正文。",
             "structural_context": "运维手册 > 告警表", "target_type": "segment",
             "target_ref": "d#seg:0", "canonical_evidence_id": "d#ce:0",
             "container_ref": None, "ordinal": 0, "lexical_eligible": True,
             "dense_eligible": True, "returnable": True,
             "facets_json": json.dumps({"document": "manual.md"}),
             "provenance_json": json.dumps({"projector_version": "1"})},
            {"representation_id": "d:s1:table_row:1",
             "representation_type": "table_row", "content_type": "table_row",
             "content_text": "告警码为A-101", "structural_context": "",
             "target_type": "table_row", "target_ref": "d#seg:1",
             "canonical_evidence_id": "d#ce:1", "container_ref": None,
             "ordinal": 1, "lexical_eligible": False, "dense_eligible": True,
             "returnable": True, "facets_json": "{}",
             "provenance_json": "{}"},
        ),
        "lexical_rows": (
            {"representation_id": "d:s1:prose:0",
             "lexical_text": "巡检 说明 正文", "tokenizer_version": "jieba-default-1"},
        ),
        "embeddings": (
            {"embedding_id": "s1:d:s1:prose:0",
             "representation_id": "d:s1:prose:0", "strategy": "structural",
             "policy_version": "emb-policy-1", "provider": "test-provider",
             "model": "embedding-3", "model_version": "v1", "dimension": 1024,
             "input_hash": "hash-0", "fallback_from": None},
        ),
        "raw_segment_count": 1,
        "representation_count": 2,
        "embedding_count": 1,
        "structure_node_count": 3,
    }


# ---------------------------------------------------------------------------
# 接口形状：与 memory 实现同构
# ---------------------------------------------------------------------------


def test_pg_three_piece_expose_memory_store_contract():
    from knowledge_mining.mining.retrieval_projection.persist import (
        MemoryAssetWriter,
    )
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryEmbeddingStore,
        MemoryRepresentationStore,
    )
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgAssetWriter,
        PgEmbeddingStore,
        PgRepresentationStore,
    )

    pool = object()
    assert {
        name for name in dir(MemoryRepresentationStore) if not name.startswith("_")
    } <= {name for name in dir(PgRepresentationStore) if not name.startswith("_")}
    assert {
        name
        for name in dir(MemoryEmbeddingStore)
        if not name.startswith("_")
    } <= {name for name in dir(PgEmbeddingStore) if not name.startswith("_")}
    assert {
        name for name in dir(MemoryAssetWriter) if not name.startswith("_")
    } <= {name for name in dir(PgAssetWriter) if not name.startswith("_")}
    # 构造只收 pool（不触库）——组合根可在无连接时组装。
    PgRepresentationStore(pool)
    PgEmbeddingStore(pool)
    PgAssetWriter(pool)


# ---------------------------------------------------------------------------
# SQL 契约：RepresentationStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_representation_replace_is_transactional_delete_then_insert():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgRepresentationStore,
    )

    pool = recording_pool()
    store = PgRepresentationStore(pool)
    count = await store.replace_for_snapshot(
        "snap-1", (_representation(), _representation("d:s1:prose:1")),
        "proj-1", document_key="manual.md",
    )
    assert count == 2

    log = statements_of(pool)
    delete = find_statement(pool, "DELETE FROM asset_retrieval_units_v2")
    insert = find_statement(pool, "INSERT INTO asset_retrieval_units_v2")
    # 事务包裹清旧插新（一次 <TRANSACTION>）
    begins = [entry for entry in log if entry[0] == "<TRANSACTION>"]
    assert len(begins) == 1
    assert log.index(delete) > log.index(begins[0])
    assert log.index(insert) > log.index(delete)
    # 参数化清旧；插入参数首列为表示 ID，lexical 两列在 M2 阶段留空
    assert delete[1] == ["snap-1"]
    assert insert[1] is not None and insert[1][0] == "d:s1:prose:0"
    assert insert[1][6] is None and insert[1][7] is None  # lexical/tokenizer
    # 27号修复 E：parent_ref/context_group_id/source_refs_json 三列
    # （12/13/14）随契约持久化；facets/provenance 顺延至 19/20
    assert insert[1][12] is None and insert[1][13] is None
    assert json.loads(insert[1][14]) == []
    assert json.loads(insert[1][19]) == {"document": "manual.md"}
    # Schema 必须由启动 migration 完成；业务热路径不得执行 DDL，否则多个
    # 文档 worker 首次并发会发生 relation lock upgrade deadlock。
    ddl = [entry for entry in log if "CREATE TABLE" in entry[0]]
    assert ddl == []


@pytest.mark.asyncio
async def test_representation_list_restores_rows_to_contract_objects():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgRepresentationStore,
    )

    rows = [{
        "representation_id": "d:s1:prose:0", "representation_type": "prose",
        "content_type": "paragraph", "content_text": "巡检说明正文。",
        "structural_context": "运维手册", "target_type": "segment",
        "target_ref": "d#seg:0", "canonical_evidence_id": "d#ce:0",
        "container_ref": None, "ordinal": 0, "lexical_eligible": True,
        "dense_eligible": True, "returnable": True,
        "facets_json": {"document": "manual.md"},
        "provenance_json": {"projector_version": "1"},
    }]
    pool = recording_pool(select_rows=rows)
    store = PgRepresentationStore(pool)
    back = await store.list_for_snapshot("snap-1")

    assert len(back) == 1
    rep = back[0]
    assert rep.representation_id == "d:s1:prose:0"
    assert rep.facets == {"document": "manual.md"}
    assert rep.provenance == {"projector_version": "1"}
    assert rep.lexical_eligible is True


@pytest.mark.asyncio
async def test_alias_replace_only_mutates_requested_staging_alias_type():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgRepresentationStore,
    )

    pool = recording_pool()
    store = PgRepresentationStore(pool)
    alias = _representation(
        "d:s1:query:0", representation_type="query_alias",
        content_type="query_alias", returnable=False,
    )
    await store.replace_aliases_for_snapshot(
        "snap-1", (alias,), "qe", document_key="manual.md",
        alias_type="query_alias",
    )

    delete = find_statement(
        pool, "DELETE FROM asset_retrieval_units_v2_staging",
    )
    assert delete[1] == ["snap-1", "query_alias"]
    assert not any(
        statement.startswith("DELETE FROM asset_retrieval_units_v2 ")
        for statement, _params in statements_of(pool)
    )


# ---------------------------------------------------------------------------
# SQL 契约：EmbeddingStore（向量本体写入）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_replace_writes_vector_literal_and_validates():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgEmbeddingStore,
    )

    pool = recording_pool()
    store = PgEmbeddingStore(pool)
    records = (_embedding_record(dimension=3),)
    vectors = ([0.5, -1.0, 2.0],)
    count = await store.replace_for_snapshot(
        "snap-1", records, "emb-policy-1",
        document_key="manual.md", vectors=vectors,
    )
    assert count == 1

    insert = find_statement(pool, "INSERT INTO asset_retrieval_embeddings_v2")
    assert insert[0].count("%s") == 13
    assert insert[1] is not None
    assert insert[1][12] == "[0.5,-1.0,2.0]"  # pgvector 文本形态
    assert insert[1][10] == "group-hash"  # context_group_hash 不丢
    # 未给向量 → NULL 向量（元数据行仍写）
    pool2 = recording_pool()
    store2 = PgEmbeddingStore(pool2)
    await store2.replace_for_snapshot(
        "snap-1", records, "emb-policy-1", document_key="manual.md",
    )
    insert2 = find_statement(pool2, "INSERT INTO asset_retrieval_embeddings_v2")
    assert insert2[1] is not None and insert2[1][12] is None


@pytest.mark.asyncio
async def test_embedding_replace_rejects_dimension_and_length_mismatch():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgEmbeddingStore,
    )

    store = PgEmbeddingStore(recording_pool())
    with pytest.raises(ValueError, match="dimension"):
        await store.replace_for_snapshot(
            "snap-1", (_embedding_record(dimension=3),), "p",
            document_key="k", vectors=([1.0, 2.0],),
        )
    with pytest.raises(ValueError, match="align"):
        await store.replace_for_snapshot(
            "snap-1", (_embedding_record(dimension=2),), "p",
            document_key="k", vectors=([1.0, 2.0], [3.0, 4.0]),
        )


@pytest.mark.asyncio
async def test_embedding_list_restores_records_without_vectors():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgEmbeddingStore,
    )

    rows = [{
        "embedding_id": "s1:d:s1:prose:0", "representation_id": "d:s1:prose:0",
        "strategy": "structural", "policy_version": "emb-policy-1",
        "provider": "p", "model": "m", "model_version": "v",
        "dimension": 1024, "input_hash": "h", "context_group_hash": "g",
        "fallback_from": "isolated",
    }]
    pool = recording_pool(select_rows=rows)
    back = await PgEmbeddingStore(pool).list_for_snapshot("snap-1")
    assert len(back) == 1
    record = back[0]
    assert record.embedding_id == "s1:d:s1:prose:0"
    assert record.strategy_input == ""  # 输入文本不入库（契约见类 docstring）
    assert record.fallback_from == "isolated"
    assert record.dimension == 1024


# ---------------------------------------------------------------------------
# SQL 契约：AssetWriter（faces → 五新表 + 元数据补齐；raw 面不写）
# ---------------------------------------------------------------------------


def test_asset_writer_writes_faces_in_single_transaction():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgAssetWriter,
    )

    pool = recording_pool()
    writer = PgAssetWriter(pool)
    assert writer.replace_for_snapshot("snap-1", _faces()) == 2

    log = statements_of(pool)
    begins = [entry for entry in log if entry[0] == "<TRANSACTION>"]
    assert len(begins) == 1
    body = log[log.index(begins[0]) + 1:]
    # 五张 v2 表清旧（快照级替换）；embeddings/raw_segments 不在清旧之列
    for table in (
        "asset_retrieval_units_v2", "asset_structure_nodes",
        "asset_structure_edges", "asset_structured_assets",
        "asset_table_cells",
    ):
        deletes = [e for e in body if e[0].startswith(f"DELETE FROM {table}")]
        assert deletes and deletes[0][1] == ["snap-1"], table
    assert not [e for e in body if e[0].startswith("DELETE FROM asset_raw_segments")]
    assert not [
        e for e in body if e[0].startswith("DELETE FROM asset_retrieval_embeddings_v2")
    ]
    assert not [e for e in body if "INSERT INTO asset_raw_segments" in e[0]]


def test_asset_writer_matches_lexical_rows_into_units():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgAssetWriter,
    )

    pool = recording_pool()
    PgAssetWriter(pool).replace_for_snapshot("snap-1", _faces())

    inserts = [
        e for e in statements_of(pool)
        if "INSERT INTO asset_retrieval_units_v2" in e[0]
    ]
    assert len(inserts) == 2
    first, second = inserts[0][1], inserts[1][1]
    assert first is not None and second is not None
    # lexical_eligible 表示命中 lexical_rows：lexical_text + tokenizer 注入
    assert first[0] == "d:s1:prose:0"
    assert first[6] == "巡检 说明 正文"
    assert first[7] == "jieba-default-1"
    # 非 lexical 表示两列为 NULL
    assert second[0] == "d:s1:table_row:1"
    assert second[6] is None and second[7] is None


def test_asset_writer_maps_structure_and_table_faces():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgAssetWriter,
    )

    pool = recording_pool()
    PgAssetWriter(pool).replace_for_snapshot("snap-1", _faces())

    node = find_statement(pool, "INSERT INTO asset_structure_nodes")
    assert node[1] is not None
    assert node[1][0] == "snap-1" and node[1][1] == "document"

    edge = find_statement(pool, "INSERT INTO asset_structure_edges")
    assert edge[1] == ["snap-1", "parent", "manual.md#seg:0",
                       "manual.md#section:告警表"]

    asset = find_statement(pool, "INSERT INTO asset_structured_assets")
    assert asset[1] is not None
    assert json.loads(asset[1][4]) == ["告警码", "功耗"]  # columns_json
    assert asset[1][7] == "asset-v2-1"  # schema_version 来自 faces

    cell = find_statement(pool, "INSERT INTO asset_table_cells")
    assert cell[1] == ["snap-1", "tbl-1", 0, 0, "告警码", "A-101", False]


def test_asset_writer_backfills_embedding_metadata_without_clobbering():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgAssetWriter,
    )

    pool = recording_pool()
    PgAssetWriter(pool).replace_for_snapshot("snap-1", _faces())

    insert = find_statement(
        pool, "INSERT INTO asset_retrieval_embeddings_v2",
    )
    # 向量本体归 PgEmbeddingStore：writer 只补元数据（DO NOTHING，向量列 NULL）
    assert "ON CONFLICT (embedding_id) DO NOTHING" in insert[0]
    assert insert[1] is not None and insert[1][12] is None
    assert insert[1][0] == "s1:d:s1:prose:0"


# ---------------------------------------------------------------------------
# 组合根接线
# ---------------------------------------------------------------------------


def test_composition_root_pg_branch_selects_pg_three_piece():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgAssetWriter,
        PgEmbeddingStore,
        PgRepresentationStore,
    )
    from knowledge_mining.mining.workflow.new_chain_services import (
        build_new_chain_services,
    )

    pool = recording_pool()
    services = build_new_chain_services(
        bucket_prefix="test-", sync_pool=pool,
    )
    assert isinstance(
        services.retrieval_project_service._representations,
        PgRepresentationStore,
    )
    assert isinstance(
        services.asset_persist_service._embeddings, PgEmbeddingStore,
    )
    assert isinstance(services.asset_persist_service._writer, PgAssetWriter)


def test_composition_root_explicit_stores_override_pg_defaults():
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryRepresentationStore,
    )
    from knowledge_mining.mining.workflow.new_chain_services import (
        build_new_chain_services,
    )

    pool = recording_pool()
    explicit = MemoryRepresentationStore()
    services = build_new_chain_services(
        bucket_prefix="test-", sync_pool=pool,
        representation_store=explicit,
    )
    assert services.retrieval_project_service._representations is explicit


def test_composition_root_memory_branch_stays_memory():
    from knowledge_mining.mining.retrieval_projection.persist import (
        MemoryAssetWriter,
    )
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryEmbeddingStore,
        MemoryRepresentationStore,
    )
    from knowledge_mining.mining.workflow.new_chain_services import (
        build_new_chain_services,
    )

    services = build_new_chain_services(bucket_prefix="test-")
    assert isinstance(
        services.retrieval_project_service._representations,
        MemoryRepresentationStore,
    )
    assert isinstance(
        services.asset_persist_service._embeddings, MemoryEmbeddingStore,
    )
    assert isinstance(services.asset_persist_service._writer, MemoryAssetWriter)


def test_embedding_facade_passes_vectors_to_store():
    """向量经 EmbeddingFacade 传入 store（PG 落向量的唯一通路）."""
    from knowledge_mining.mining.retrieval_projection.embedding import (
        EmbeddingFacade,
    )

    class _RecordingEmbeddingStore:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def replace_for_snapshot(
            self, snapshot_id, records, policy_version, *,
            document_key, vectors=(),
        ) -> int:
            self.calls.append({
                "snapshot_id": snapshot_id, "records": records,
                "vectors": vectors,
            })
            return len(records)

    class _Generator:
        capabilities = frozenset({"structural"})
        model = "embedding-3"

        def describe(self):
            return {"provider": "p", "model": "m", "version": "v",
                    "dimension": 3}

        def embed_batch(self, texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

    class _RepStore:
        async def list_for_snapshot(self, snapshot_id):
            return (_representation(),)

    store = _RecordingEmbeddingStore()
    facade = EmbeddingFacade(
        representation_store=_RepStore(), embedding_store=store,
        generator=_Generator(),
    )
    outcome = facade.embed_for_snapshot(snapshot_id="snap-1", params={})
    assert outcome.written == 1
    assert store.calls[0]["vectors"] == ([0.1, 0.2, 0.3],)


# ---------------------------------------------------------------------------
# 真库门禁：round-trip + 真约束（KB_RUN_POSTGRES_ACCEPTANCE=1）
# ---------------------------------------------------------------------------

_pg_gate = pytest.mark.skipif(
    os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1",
    reason="set KB_RUN_POSTGRES_ACCEPTANCE=1 to run PostgreSQL acceptance",
)


@pytest_asyncio.fixture
async def pg_pool(db_config, _ensure_schema):  # noqa: ANN001
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        db_config.conninfo,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@_pg_gate
@pytest.mark.asyncio
async def test_pg_representation_round_trip_and_replace(pg_pool):
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgRepresentationStore,
    )

    store = PgRepresentationStore(pg_pool)
    reps = (
        _representation(ordinal=0),
        _representation("d:s1:table_row:1", representation_type="table_row",
                        content_type="table_row", lexical_eligible=False),
    )
    assert await store.replace_for_snapshot(
        "snap_pg_rp", reps, "proj-1", document_key="manual.md",
    ) == 2
    back = await store.list_for_snapshot("snap_pg_rp")
    assert [r.representation_id for r in back] == [
        "d:s1:prose:0", "d:s1:table_row:1",
    ]
    assert back[0].facets == {"document": "manual.md"}
    assert back[0].provenance["projector_version"] == "1"
    assert back[1].lexical_eligible is False

    # 快照级替换：重投影后只剩新表示，不累积
    assert await store.replace_for_snapshot(
        "snap_pg_rp", (_representation("d:s1:prose:9", ordinal=9),),
        "proj-2", document_key="manual.md",
    ) == 1
    assert [r.representation_id for r in await store.list_for_snapshot(
        "snap_pg_rp"
    )] == ["d:s1:prose:9"]


@_pg_gate
@pytest.mark.asyncio
async def test_pg_embedding_store_persists_real_vectors(pg_pool):
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgEmbeddingStore,
    )

    dimension = 1024  # VECTOR(1024) 真约束
    vector = [round(0.001 * (i + 1), 6) for i in range(dimension)]
    records = (
        _embedding_record(
            embedding_id="snap_pg_emb:d:s1:prose:0", dimension=dimension,
            fallback_from="isolated",
        ),
    )
    store = PgEmbeddingStore(pg_pool)
    assert await store.replace_for_snapshot(
        "snap_pg_emb", records, "emb-policy-1",
        document_key="manual.md", vectors=(vector,),
    ) == 1

    back = await store.list_for_snapshot("snap_pg_emb")
    assert len(back) == 1
    assert back[0].embedding_id == "snap_pg_emb:d:s1:prose:0"
    assert back[0].fallback_from == "isolated"
    assert back[0].strategy_input == ""

    async with pg_pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT embedding_vector_vec IS NOT NULL AS has_vector "
            "FROM asset_retrieval_embeddings_v2 "
            "WHERE embedding_id = %s",
            ["snap_pg_emb:d:s1:prose:0"],
        )).fetchone()
    assert row is not None and row["has_vector"] is True


@_pg_gate
@pytest.mark.asyncio
async def test_pg_asset_writer_persists_faces_and_lexical(pg_pool):
    from knowledge_mining.mining.retrieval_projection.embedding import (
        EmbeddingRecord,
    )
    from knowledge_mining.mining.retrieval_projection.persist import (
        AssetPersistService,
    )
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryEmbeddingStore,
        MemoryRepresentationStore,
    )
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgAssetWriter,
    )
    from knowledge_mining.mining.segment_compiler.repositories_memory import (
        MemorySegmentStore,
    )
    from knowledge_mining.tests.test_m5_three_projection import _reps, _segments

    seg_store = MemorySegmentStore()
    rep_store = MemoryRepresentationStore()
    emb_store = MemoryEmbeddingStore()
    await seg_store.replace_for_snapshot(
        "s1", _segments(), "segc", document_key="manual.md",
    )
    await rep_store.replace_for_snapshot(
        "s1", _reps(), "proj", document_key="manual.md",
    )
    await emb_store.replace_for_snapshot(
        "s1",
        (EmbeddingRecord(
            embedding_id="s1:d:s1:prose:0", representation_id="d:s1:prose:0",
            strategy="structural", strategy_input="x", input_hash="h",
            policy_version="emb-policy-1", provider="p", model="m",
            model_version="v", dimension=8, context_group_hash="g",
        ),),
        "emb-policy-1", document_key="manual.md",
    )
    writer = PgAssetWriter(pg_pool)
    service = AssetPersistService(
        segment_store=seg_store,
        representation_store=rep_store,
        embedding_store=emb_store,
        writer=writer,
    )
    outcome = service.persist_for_snapshot(
        snapshot_id="s1", document_ref="manual.md",
    )
    assert outcome.counts["representations"] >= 1

    async with pg_pool.connection() as conn:
        units = await (await conn.execute(
            """SELECT representation_id, lexical_text, tokenizer_version,
                      search_vector IS NOT NULL AS has_fts
               FROM asset_retrieval_units_v2 WHERE snapshot_id = %s""",
            ["s1"],
        )).fetchall()
        nodes = await (await conn.execute(
            "SELECT COUNT(*) AS n FROM asset_structure_nodes "
            "WHERE snapshot_id = %s", ["s1"],
        )).fetchone()
        cells = await (await conn.execute(
            "SELECT COUNT(*) AS n FROM asset_table_cells "
            "WHERE snapshot_id = %s", ["s1"],
        )).fetchone()
    assert units, "units_v2 rows missing"
    lexical_units = [u for u in units if u["lexical_text"]]
    assert lexical_units, "lexical_text not injected from lexical_rows"
    assert lexical_units[0]["tokenizer_version"] == "jieba-default-1"
    assert all(u["has_fts"] for u in lexical_units)  # 生成列随 lexical 落表
    assert nodes["n"] >= 3
    assert cells["n"] >= 1

    # 幂等重跑：行数不翻倍（快照级替换）
    service.persist_for_snapshot(snapshot_id="s1", document_ref="manual.md")
    async with pg_pool.connection() as conn:
        again = await (await conn.execute(
            "SELECT COUNT(*) AS n FROM asset_retrieval_units_v2 "
            "WHERE snapshot_id = %s", ["s1"],
        )).fetchone()
    assert again["n"] == len(units)
