"""三面资产 v2 契约常量（批次8 M5）.

生产 DDL 已迁移到 ``013_retrieval_assets_v2_staging.sql``，由 pg_schema
在启动阶段维护。此模块的 statement 集仅保留给契约/内存测试与显式开发
工具，禁止 repository 热路径调用。Java 检索侧只以 schema version 化
mapper 消费。FTS 契约：lexical_text 由 mining 预分词
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
        parent_ref          TEXT,
        context_group_id    TEXT,
        source_refs_json    JSONB NOT NULL DEFAULT '[]',
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
    # 27号审查修复 B（24号 §7/§320：snapshot readiness 持久化）——四能力
    # 事实随三面资产原子落库；finalize 据此门禁发布，inspect 优先读冻结值。
    """
    CREATE TABLE IF NOT EXISTS asset_snapshot_readiness (
        snapshot_id      TEXT PRIMARY KEY,
        document_ref     TEXT NOT NULL,
        readiness_json   JSONB NOT NULL,
        schema_version   TEXT NOT NULL,
        tokenizer_version TEXT,
        computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # 27号审查修复 E（24号 §5.4 契约）：units_v2 补 source_refs/parent_ref/
    # context_group_id——CREATE IF NOT EXISTS 对存量表是空操作，ALTER 兜底。
    """
    ALTER TABLE asset_retrieval_units_v2
        ADD COLUMN IF NOT EXISTS parent_ref TEXT
    """,
    """
    ALTER TABLE asset_retrieval_units_v2
        ADD COLUMN IF NOT EXISTS context_group_id TEXT
    """,
    """
    ALTER TABLE asset_retrieval_units_v2
        ADD COLUMN IF NOT EXISTS source_refs_json JSONB NOT NULL DEFAULT '[]'
    """,
)


def _staging_statements() -> tuple[str, ...]:
    """29号 R03（Wave 2）：派生资产 staging 表——与 final 同构（表名后缀）。

    project/embedding/persist 全程只写 staging；mining_finalize 在 Build
    组装事务内把 staging 原子晋升到 final——未发布/失败的 run 不再触碰
    活动 Build 读到的资产。源证据面（asset_raw_segments）内容寻址、随
    快照提交即冻结，不参与 staging。
    """
    import re as _re

    out: list[str] = []
    for stmt in ASSET_SCHEMA_V2_STATEMENTS:
        stripped = stmt.strip()
        match = _re.match(
            r"CREATE TABLE IF NOT EXISTS (\w+) ", stripped, _re.IGNORECASE,
        )
        if match is None:
            continue
        name = match.group(1)
        # readiness 的 computed_at 默认值在晋升列清单之外，结构同构即可。
        out.append(stripped.replace(
            f"CREATE TABLE IF NOT EXISTS {name}",
            f"CREATE TABLE IF NOT EXISTS {name}_staging", 1,
        ))
    for table in (
        "asset_retrieval_units_v2_staging",
        "asset_retrieval_embeddings_v2_staging",
        "asset_structure_nodes_staging",
        "asset_structure_edges_staging",
        "asset_structured_assets_staging",
        "asset_table_cells_staging",
        "asset_snapshot_readiness_staging",
    ):
        out.append(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_snapshot "
            f"ON {table} (snapshot_id)"
        )
    return tuple(out)


#: 晋升列清单（final 与 staging 严格同序；生成列/默认列不参与）。
PROMOTE_TABLE_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "asset_retrieval_units_v2",
        (
            "representation_id", "snapshot_id", "representation_type",
            "content_type", "content_text", "structural_context",
            "lexical_text", "tokenizer_version", "target_type", "target_ref",
            "canonical_evidence_id", "container_ref", "parent_ref",
            "context_group_id", "source_refs_json", "ordinal",
            "lexical_eligible", "dense_eligible", "returnable",
            "facets_json", "provenance_json",
        ),
    ),
    (
        "asset_retrieval_embeddings_v2",
        (
            "embedding_id", "snapshot_id", "representation_id", "strategy",
            "policy_version", "provider", "model", "model_version",
            "dimension", "input_hash", "context_group_hash", "fallback_from",
            "embedding_vector_vec",
        ),
    ),
    (
        "asset_structure_nodes",
        ("snapshot_id", "node_type", "ref", "parent_ref", "ordinal", "title",
         "level", "block_type"),
    ),
    (
        "asset_structure_edges",
        ("snapshot_id", "relation", "from_ref", "to_ref"),
    ),
    (
        "asset_structured_assets",
        ("snapshot_id", "asset_ref", "asset_type", "table_ref",
         "columns_json", "row_count", "readiness", "schema_version"),
    ),
    (
        "asset_table_cells",
        ("snapshot_id", "table_ref", "row_index", "column_index",
         "column_name", "value", "is_header"),
    ),
    (
        "asset_snapshot_readiness",
        ("snapshot_id", "document_ref", "readiness_json", "schema_version",
         "tokenizer_version"),
    ),
)

ASSET_SCHEMA_V2_STATEMENTS = ASSET_SCHEMA_V2_STATEMENTS + _staging_statements()


def ensure_asset_schema_v2(conn: Any) -> None:
    """Deprecated developer helper; production uses migration 013.

    Do not call this from repositories or document-processing workers.
    """
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
