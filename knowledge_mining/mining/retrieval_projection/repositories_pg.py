"""PostgreSQL repositories for the three-face asset projection (批次8 M5 生产化).

PgRepresentationStore / PgEmbeddingStore / PgAssetWriter —— 生产落库三件套；
memory 实现（repositories_memory）仅测试/开发。快照级替换语义与 Memory*
一致：幂等重跑 = 事务内先删该快照全部行再整体插入，不累积重复。

DDL 真相源：同目录 schema.py（``ASSET_SCHEMA_V2_STATEMENTS``）。各 store
首次使用时逐条幂等执行（CREATE IF NOT EXISTS；并发首跑的 Duplicate 冲突
按 infra/pg_schema.py 同款惯例吞掉），DDL 归属约定见 24 号 §5.8。

与 persist.py faces 契约对齐的两处刻意取舍（最小且一致方案）：

1. **raw 面不写**：``asset_raw_segments`` 在已部署库上是 002 DDL 的 legacy
   形态（document_snapshot_id 列 + FK→asset_document_snapshots），且同一条
   工作流里 segment_compile 的 PgSegmentStore 已按快照替换写入该表；
   schema.py 对该表名的 CREATE IF NOT EXISTS 在存量库上是空操作，v2 列
   形态的 INSERT 必然 UndefinedColumn。faces["raw_segments"] 只参与计数。
2. **向量本体归 PgEmbeddingStore**：faces["embeddings"] 不含向量（只含
   provenance 元数据）；EmbeddingFacade 产出的向量经
   ``replace_for_snapshot(..., vectors=...)`` 传入（Memory 实现忽略该参数）。
   PgAssetWriter 对 embeddings 面只补元数据行（ON CONFLICT DO NOTHING），
   不删不覆盖——避免 M5 的元数据重插抹掉 M4 已写入的向量。
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from knowledge_mining.mining.retrieval_projection.schema import (
    ASSET_SCHEMA_V2_STATEMENTS,
)

_UNITS_DELETE = "DELETE FROM asset_retrieval_units_v2 WHERE snapshot_id = %s"

_UNITS_INSERT = """
    INSERT INTO asset_retrieval_units_v2 (
        representation_id, snapshot_id, representation_type, content_type,
        content_text, structural_context, lexical_text, tokenizer_version,
        target_type, target_ref, canonical_evidence_id, container_ref,
        parent_ref, context_group_id, source_refs_json,
        ordinal, lexical_eligible, dense_eligible, returnable,
        facets_json, provenance_json
    ) VALUES (
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s::jsonb
    )
"""

_UNITS_SELECT = """
    SELECT representation_id, representation_type, content_type, content_text,
           structural_context, target_type, target_ref, canonical_evidence_id,
           container_ref, parent_ref, context_group_id, source_refs_json,
           ordinal, lexical_eligible, dense_eligible,
           returnable, facets_json, provenance_json
    FROM asset_retrieval_units_v2
    WHERE snapshot_id = %s
    ORDER BY ordinal, representation_id
"""

_EMBEDDINGS_DELETE = (
    "DELETE FROM asset_retrieval_embeddings_v2 WHERE snapshot_id = %s"
)

_EMBEDDINGS_INSERT = """
    INSERT INTO asset_retrieval_embeddings_v2 (
        embedding_id, snapshot_id, representation_id, strategy,
        policy_version, provider, model, model_version, dimension,
        input_hash, context_group_hash, fallback_from, embedding_vector_vec
    ) VALUES (
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector
    )
"""

_EMBEDDINGS_UPSERT_META = _EMBEDDINGS_INSERT + """
    ON CONFLICT (embedding_id) DO NOTHING
"""

_EMBEDDINGS_SELECT = """
    SELECT embedding_id, representation_id, strategy, policy_version,
           provider, model, model_version, dimension, input_hash,
           context_group_hash, fallback_from
    FROM asset_retrieval_embeddings_v2
    WHERE snapshot_id = %s
    ORDER BY embedding_id
"""

_STRUCTURE_NODES_DELETE = (
    "DELETE FROM asset_structure_nodes WHERE snapshot_id = %s"
)
_STRUCTURE_NODES_INSERT = """
    INSERT INTO asset_structure_nodes (
        snapshot_id, node_type, ref, parent_ref, ordinal, title, level,
        block_type
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
"""

_STRUCTURE_EDGES_DELETE = (
    "DELETE FROM asset_structure_edges WHERE snapshot_id = %s"
)
_STRUCTURE_EDGES_INSERT = """
    INSERT INTO asset_structure_edges (snapshot_id, relation, from_ref, to_ref)
    VALUES (%s,%s,%s,%s)
"""

_STRUCTURED_ASSETS_DELETE = (
    "DELETE FROM asset_structured_assets WHERE snapshot_id = %s"
)
_STRUCTURED_ASSETS_INSERT = """
    INSERT INTO asset_structured_assets (
        snapshot_id, asset_ref, asset_type, table_ref, columns_json,
        row_count, readiness, schema_version
    ) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
"""

_TABLE_CELLS_DELETE = "DELETE FROM asset_table_cells WHERE snapshot_id = %s"

_READINESS_DELETE = (
    "DELETE FROM asset_snapshot_readiness WHERE snapshot_id = %s"
)

_READINESS_INSERT = """
    INSERT INTO asset_snapshot_readiness (
        snapshot_id, document_ref, readiness_json, schema_version,
        tokenizer_version
    ) VALUES (%s, %s, %s::jsonb, %s, %s)
"""
_TABLE_CELLS_INSERT = """
    INSERT INTO asset_table_cells (
        snapshot_id, table_ref, row_index, column_index, column_name, value,
        is_header
    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
"""


class _PgSchemaBound:
    """pool 持有 + schema.py v2 DDL 的首次使用幂等初始化（DDL 归 mining）."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._schema_ready = False

    async def _ensure_schema(self, conn: Any) -> None:
        if self._schema_ready:
            return
        import psycopg.errors

        for statement in ASSET_SCHEMA_V2_STATEMENTS:
            try:
                await conn.execute(statement)
            except (
                psycopg.errors.DuplicateTable,
                psycopg.errors.DuplicateObject,
            ):
                pass  # 已存在/并发首跑撞名 —— 幂等（pg_schema.py 同款惯例）
        self._schema_ready = True


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return json.loads(value)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return json.loads(value)


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _vector_literal(vector: Sequence[float]) -> str:
    """list[float] → pgvector 文本输入形态（'[1.0,2.0]'；写入用 %s::vector）."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


class PgRepresentationStore(_PgSchemaBound):
    """PG ``RepresentationStore``：asset_retrieval_units_v2（快照级替换）.

    M2（retrieval_unit_project）落库；lexical_text/tokenizer_version 留空，
    由 M5 的 PgAssetWriter 按 lexical_rows 补齐（FTS 面在 persist 阶段定型）。
    """

    async def replace_for_snapshot(
        self,
        snapshot_id: str,
        representations: tuple,
        projector_fingerprint: str,
        *,
        document_key: str,
    ) -> int:
        async with self._pool.connection() as conn:
            await self._ensure_schema(conn)
            async with conn.transaction():
                await conn.execute(_UNITS_DELETE, [snapshot_id])
                for rep in representations:
                    await conn.execute(
                        _UNITS_INSERT,
                        [
                            rep.representation_id, snapshot_id,
                            rep.representation_type, rep.content_type,
                            rep.content_text, rep.structural_context,
                            None, None,
                            rep.target_type, rep.target_ref,
                            rep.canonical_evidence_id, rep.container_ref,
                            rep.parent_ref, rep.context_group_id,
                            json.dumps(
                                [dict(r) for r in rep.source_refs],
                                ensure_ascii=False,
                            ),
                            rep.ordinal, bool(rep.lexical_eligible),
                            bool(rep.dense_eligible), bool(rep.returnable),
                            json.dumps(dict(rep.facets), ensure_ascii=False),
                            json.dumps(
                                dict(rep.provenance), ensure_ascii=False,
                            ),
                        ],
                    )
        return len(representations)

    async def list_for_snapshot(self, snapshot_id: str) -> tuple:
        from knowledge_mining.mining.contracts.retrieval_projection import (
            RetrieRepresentation,
        )

        async with self._pool.connection() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(_UNITS_SELECT, [snapshot_id])
            rows = await cursor.fetchall()
        return tuple(
            RetrieRepresentation(
                representation_id=str(row["representation_id"]),
                representation_type=str(row["representation_type"]),
                content_type=str(row["content_type"]),
                content_text=str(row["content_text"]),
                target_type=str(row["target_type"]),
                target_ref=str(row["target_ref"]),
                canonical_evidence_id=str(row["canonical_evidence_id"]),
                structural_context=str(row.get("structural_context") or ""),
                container_ref=_as_str(row.get("container_ref")),
                parent_ref=_as_str(row.get("parent_ref")),
                context_group_id=_as_str(row.get("context_group_id")),
                source_refs=tuple(
                    dict(r) for r in _as_list(row.get("source_refs_json"))
                    if isinstance(r, dict)
                ),
                ordinal=int(row.get("ordinal") or 0),
                lexical_eligible=bool(row.get("lexical_eligible")),
                dense_eligible=bool(row.get("dense_eligible")),
                returnable=bool(row.get("returnable")),
                facets=_as_dict(row.get("facets_json")),
                provenance=_as_dict(row.get("provenance_json")),
            )
            for row in rows
        )

    def projector_fingerprint(self, snapshot_id: str) -> str | None:
        """v2 DDL 无指纹列——幂等由快照级替换保证（Memory 实现的暂存字段）."""
        return None

    async def replace_aliases_for_snapshot(
        self,
        snapshot_id: str,
        aliases: tuple,
        fingerprint: str,
        *,
        document_key: str,
    ) -> int:
        """27号审查修复：只替换 alias 子集（query_alias/summary_alias）——
        别名算子（M3）幂等重跑不得清空该快照的基础表示。"""
        async with self._pool.connection() as conn:
            await self._ensure_schema(conn)
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM asset_retrieval_units_v2 "
                    "WHERE snapshot_id = %s "
                    "AND representation_type IN ('query_alias', 'summary_alias')",
                    [snapshot_id],
                )
                for rep in aliases:
                    await conn.execute(
                        _UNITS_INSERT,
                        [
                            rep.representation_id, snapshot_id,
                            rep.representation_type, rep.content_type,
                            rep.content_text, rep.structural_context,
                            None, None,
                            rep.target_type, rep.target_ref,
                            rep.canonical_evidence_id, rep.container_ref,
                            rep.parent_ref, rep.context_group_id,
                            json.dumps(
                                [dict(r) for r in rep.source_refs],
                                ensure_ascii=False,
                            ),
                            rep.ordinal, bool(rep.lexical_eligible),
                            bool(rep.dense_eligible), bool(rep.returnable),
                            json.dumps(dict(rep.facets), ensure_ascii=False),
                            json.dumps(
                                dict(rep.provenance), ensure_ascii=False,
                            ),
                        ],
                    )
        return len(aliases)


class PgEmbeddingStore(_PgSchemaBound):
    """PG ``EmbeddingStore``：asset_retrieval_embeddings_v2（快照级替换）.

    向量本体经 ``vectors``（与 records 等长平行序列）写入 ``%s::vector``；
    dimension 与向量长度不一致时显式拒绝。strategy_input 不入库（重构
    EmbeddingRecord 时置空——消费方只读 provenance 字段）。
    """

    async def replace_for_snapshot(
        self,
        snapshot_id: str,
        records: tuple,
        policy_version: str,
        *,
        document_key: str,
        vectors: tuple = (),
    ) -> int:
        prepared = _prepare_embedding_rows(
            snapshot_id, records, vectors=vectors,
        )
        async with self._pool.connection() as conn:
            await self._ensure_schema(conn)
            async with conn.transaction():
                await conn.execute(_EMBEDDINGS_DELETE, [snapshot_id])
                for params in prepared:
                    await conn.execute(_EMBEDDINGS_INSERT, params)
        return len(records)

    async def list_for_snapshot(self, snapshot_id: str) -> tuple:
        from knowledge_mining.mining.retrieval_projection.embedding import (
            EmbeddingRecord,
        )

        async with self._pool.connection() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(_EMBEDDINGS_SELECT, [snapshot_id])
            rows = await cursor.fetchall()
        return tuple(
            EmbeddingRecord(
                embedding_id=str(row["embedding_id"]),
                representation_id=str(row["representation_id"]),
                strategy=str(row["strategy"]),
                strategy_input="",  # 输入文本不入库（见类 docstring）
                input_hash=str(row["input_hash"]),
                policy_version=str(row["policy_version"]),
                provider=str(row["provider"]),
                model=str(row["model"]),
                model_version=str(row["model_version"]),
                dimension=int(row["dimension"]),
                context_group_hash=str(row.get("context_group_hash") or ""),
                fallback_from=_as_str(row.get("fallback_from")),
            )
            for row in rows
        )


def _prepare_embedding_rows(
    snapshot_id: str,
    records: tuple,
    *,
    vectors: tuple = (),
) -> list[list[Any]]:
    if vectors and len(vectors) != len(records):
        raise ValueError(
            f"vectors ({len(vectors)}) must align with records "
            f"({len(records)})"
        )
    prepared: list[list[Any]] = []
    for index, record in enumerate(records):
        vector = vectors[index] if index < len(vectors) else None
        literal: str | None = None
        if vector:
            if len(vector) != record.dimension:
                raise ValueError(
                    f"embedding {record.embedding_id}: dimension "
                    f"{record.dimension} != vector length {len(vector)}"
                )
            literal = _vector_literal(vector)
        prepared.append([
            record.embedding_id, snapshot_id, record.representation_id,
            record.strategy, record.policy_version, record.provider,
            record.model, record.model_version, record.dimension,
            record.input_hash, record.context_group_hash,
            record.fallback_from, literal,
        ])
    return prepared


class PgAssetWriter(_PgSchemaBound):
    """PG ``AssetWriter``：三面资产按快照整体落库（faces 见 persist.py）.

    同步入口（AssetPersistService 直接调用）；内部经 async_bridge.run_sync
    驱动 PG 写入，事务包裹全部清旧插新——中途失败不得留下半个快照。
    raw 面与向量本体的归属取舍见模块 docstring。
    """

    def replace_for_snapshot(
        self, snapshot_id: str, faces: Mapping[str, Any]
    ) -> int:
        from .async_bridge import run_sync

        return run_sync(self._replace_for_snapshot(snapshot_id, faces))

    async def _replace_for_snapshot(
        self, snapshot_id: str, faces: Mapping[str, Any]
    ) -> int:
        representations = tuple(faces.get("representations") or ())
        lexical_by_id = {
            row["representation_id"]: row
            for row in (faces.get("lexical_rows") or ())
        }
        async with self._pool.connection() as conn:
            await self._ensure_schema(conn)
            async with conn.transaction():
                for delete in (
                    _UNITS_DELETE, _STRUCTURE_NODES_DELETE,
                    _STRUCTURE_EDGES_DELETE, _STRUCTURED_ASSETS_DELETE,
                    _TABLE_CELLS_DELETE, _READINESS_DELETE,
                ):
                    await conn.execute(delete, [snapshot_id])
                await self._insert_units(
                    conn, snapshot_id, representations, lexical_by_id,
                )
                await self._insert_structure(conn, snapshot_id, faces)
                await self._insert_tables(conn, snapshot_id, faces)
                await self._insert_embedding_meta(conn, snapshot_id, faces)
                await self._insert_readiness(conn, snapshot_id, faces)
        return int(faces.get("representation_count", len(representations)))

    async def _insert_units(
        self, conn: Any, snapshot_id: str, representations: tuple,
        lexical_by_id: Mapping[str, Mapping[str, Any]],
    ) -> None:
        for rep in representations:
            lexical = lexical_by_id.get(rep["representation_id"])
            await conn.execute(
                _UNITS_INSERT,
                [
                    rep["representation_id"], snapshot_id,
                    rep["representation_type"], rep["content_type"],
                    rep["content_text"],
                    rep.get("structural_context") or "",
                    lexical.get("lexical_text") if lexical else None,
                    lexical.get("tokenizer_version") if lexical else None,
                    rep["target_type"], rep["target_ref"],
                    rep["canonical_evidence_id"], rep.get("container_ref"),
                    rep.get("parent_ref"),
                    rep.get("context_group_id"),
                    rep.get("source_refs_json") or "[]",
                    rep.get("ordinal") or 0,
                    bool(rep.get("lexical_eligible", True)),
                    bool(rep.get("dense_eligible", True)),
                    bool(rep.get("returnable", True)),
                    rep.get("facets_json") or "{}",
                    rep.get("provenance_json") or "{}",
                ],
            )

    async def _insert_structure(
        self, conn: Any, snapshot_id: str, faces: Mapping[str, Any],
    ) -> None:
        for node in faces.get("structure_nodes") or ():
            await conn.execute(
                _STRUCTURE_NODES_INSERT,
                [
                    snapshot_id, node.get("node_type"), node.get("ref"),
                    node.get("parent_ref"), node.get("ordinal"),
                    node.get("title"), node.get("level"),
                    node.get("block_type"),
                ],
            )
        for edge in faces.get("structure_edges") or ():
            await conn.execute(
                _STRUCTURE_EDGES_INSERT,
                [
                    snapshot_id, edge["relation"], edge["from_ref"],
                    edge["to_ref"],
                ],
            )

    async def _insert_tables(
        self, conn: Any, snapshot_id: str, faces: Mapping[str, Any],
    ) -> None:
        schema_version = str(faces.get("schema_version") or "")
        for asset in faces.get("table_assets") or ():
            await conn.execute(
                _STRUCTURED_ASSETS_INSERT,
                [
                    snapshot_id, asset["asset_ref"], asset["asset_type"],
                    asset.get("table_ref"),
                    json.dumps(list(asset.get("columns") or ())),
                    asset.get("row_count"), asset.get("readiness"),
                    schema_version,
                ],
            )
        for cell in faces.get("table_cells") or ():
            await conn.execute(
                _TABLE_CELLS_INSERT,
                [
                    snapshot_id, cell["table_ref"], cell["row"],
                    cell["column_index"], cell["column"], cell["value"],
                    bool(cell.get("is_header", False)),
                ],
            )

    async def _insert_embedding_meta(
        self, conn: Any, snapshot_id: str, faces: Mapping[str, Any],
    ) -> None:
        """只补元数据行（ON CONFLICT DO NOTHING）——向量本体归 PgEmbeddingStore."""
        for record in faces.get("embeddings") or ():
            await conn.execute(
                _EMBEDDINGS_UPSERT_META,
                [
                    record["embedding_id"], snapshot_id,
                    record["representation_id"], record["strategy"],
                    record["policy_version"], record["provider"],
                    record["model"], record["model_version"],
                    record["dimension"], record["input_hash"],
                    record.get("context_group_hash"),
                    record.get("fallback_from"), None,
                ],
            )

    async def _insert_readiness(
        self, conn: Any, snapshot_id: str, faces: Mapping[str, Any],
    ) -> None:
        """四能力 readiness 事实随三面原子落库（27号审查修复 B）.

        无 readiness 的 faces（防御：异常形状）跳过——不阻塞三面写入，
        finalize 门禁对缺行快照按『未知』处理并拒绝发布。
        """
        readiness = faces.get("readiness")
        if not readiness:
            return
        await conn.execute(
            _READINESS_INSERT,
            [
                snapshot_id,
                str(faces.get("document_ref") or snapshot_id),
                json.dumps(dict(readiness), ensure_ascii=False),
                str(faces.get("schema_version") or ""),
                str(faces.get("tokenizer_version") or ""),
            ],
        )


__all__ = [
    "PgAssetWriter",
    "PgEmbeddingStore",
    "PgRepresentationStore",
]
