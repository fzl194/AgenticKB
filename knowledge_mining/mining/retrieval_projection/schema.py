"""三面资产 v2 DDL（批次8 M5，24 号 §5.8/DDL 归属约定）.

DDL 由 mining 侧唯一维护（幂等 CREATE IF NOT EXISTS）；Java 检索侧只以
schema version 化 mapper 消费。FTS 契约：lexical_text 由 mining 预分词
（tokenize_for_search/jieba），PG 端用 'simple' 配置建 tsvector——两侧
分词器版本一致性由 TOKENIZER_VERSION 冻结进 build manifest。
"""
from __future__ import annotations

TOKENIZER_VERSION = "jieba-default-1"
ASSET_SCHEMA_VERSION = "asset-v2-1"

ASSET_SCHEMA_V2_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS asset_raw_segments (
        snapshot_id     TEXT NOT NULL,
        segment_index   INTEGER NOT NULL,
        block_type      TEXT NOT NULL,
        raw_text        TEXT NOT NULL,
        heading_chain_json JSONB NOT NULL DEFAULT '[]',
        metadata_json   JSONB NOT NULL DEFAULT '{}',
        token_count     INTEGER,
        PRIMARY KEY (snapshot_id, segment_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_structure_nodes (
        snapshot_id TEXT NOT NULL,
        node_type   TEXT NOT NULL,
        ref         TEXT NOT NULL,
        parent_ref  TEXT,
        ordinal     INTEGER,
        title       TEXT,
        level       INTEGER,
        block_type  TEXT,
        PRIMARY KEY (snapshot_id, ref)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_structure_edges (
        snapshot_id TEXT NOT NULL,
        relation    TEXT NOT NULL,
        from_ref    TEXT NOT NULL,
        to_ref      TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, relation, from_ref, to_ref)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_structured_assets (
        snapshot_id TEXT NOT NULL,
        asset_ref   TEXT NOT NULL,
        asset_type  TEXT NOT NULL,
        table_ref   TEXT,
        columns_json JSONB NOT NULL DEFAULT '[]',
        row_count   INTEGER,
        readiness   TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, asset_ref)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_table_cells (
        snapshot_id  TEXT NOT NULL,
        table_ref    TEXT NOT NULL,
        row_index    INTEGER NOT NULL,
        column_index INTEGER NOT NULL,
        column_name  TEXT NOT NULL,
        value        TEXT NOT NULL,
        is_header    BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (snapshot_id, table_ref, row_index, column_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_retrieval_units_v2 (
        representation_id   TEXT PRIMARY KEY,
        snapshot_id         TEXT NOT NULL,
        representation_type TEXT NOT NULL,
        content_type        TEXT NOT NULL,
        content_text        TEXT NOT NULL,
        structural_context  TEXT NOT NULL DEFAULT '',
        lexical_text        TEXT,
        target_type         TEXT NOT NULL,
        target_ref          TEXT NOT NULL,
        canonical_evidence_id TEXT NOT NULL,
        container_ref       TEXT,
        ordinal             INTEGER NOT NULL DEFAULT 0,
        lexical_eligible    BOOLEAN NOT NULL DEFAULT TRUE,
        dense_eligible      BOOLEAN NOT NULL DEFAULT TRUE,
        returnable          BOOLEAN NOT NULL DEFAULT TRUE,
        facets_json         JSONB NOT NULL DEFAULT '{}',
        provenance_json     JSONB NOT NULL DEFAULT '{}',
        tokenizer_version   TEXT,
        search_vector       TSVECTOR
            GENERATED ALWAYS AS (to_tsvector('simple', COALESCE(lexical_text, ''))) STORED
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_aru_v2_snapshot
        ON asset_retrieval_units_v2 (snapshot_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_aru_v2_canonical
        ON asset_retrieval_units_v2 (snapshot_id, canonical_evidence_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_aru_v2_search
        ON asset_retrieval_units_v2 USING GIN (search_vector)
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_retrieval_embeddings_v2 (
        embedding_id      TEXT PRIMARY KEY,
        snapshot_id       TEXT NOT NULL,
        representation_id TEXT NOT NULL,
        strategy          TEXT NOT NULL,
        policy_version    TEXT NOT NULL,
        provider          TEXT NOT NULL,
        model             TEXT NOT NULL,
        model_version     TEXT NOT NULL,
        dimension         INTEGER NOT NULL,
        input_hash        TEXT NOT NULL,
        context_group_hash TEXT,
        fallback_from     TEXT,
        embedding_vector_vec VECTOR(1024)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_are_v2_snapshot
        ON asset_retrieval_embeddings_v2 (snapshot_id)
    """,
)


def ensure_asset_schema_v2(conn: Any) -> None:
    """幂等建表（mining 唯一维护；DDL 归属约定见 24 号 §5.8）."""
    with conn.cursor() as cursor:
        for statement in ASSET_SCHEMA_V2_STATEMENTS:
            cursor.execute(statement)
    conn.commit()


__all__ = [
    "ASSET_SCHEMA_VERSION",
    "ASSET_SCHEMA_V2_STATEMENTS",
    "TOKENIZER_VERSION",
    "ensure_asset_schema_v2",
]
