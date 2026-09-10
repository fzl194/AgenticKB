"""三面资产 v2 契约常量（批次8 M5；代码瘦身批次3 收口）.

生产 DDL 唯一真相源是 ``databases/asset_core/schemas/013*.sql``（由 pg_schema
启动迁移维护）。本模块曾持有的 Python DDL 副本（ASSET_SCHEMA_V2_STATEMENTS，
其中 asset_raw_segments 列名已与生产 DDL 漂移）与 deprecated 开发助手
ensure_asset_schema_v2() 已删除。Java 检索侧只以 schema version 化 mapper
消费。FTS 契约：lexical_text 由 mining 预分词（tokenize_for_search/jieba），
PG 端用 'simple' 配置建 tsvector——两侧分词器版本一致性由 TOKENIZER_VERSION
冻结进 build manifest。
"""
from __future__ import annotations

TOKENIZER_VERSION = "jieba-default-1"
ASSET_SCHEMA_VERSION = "asset-v2-1"

#: 晋升列清单（final 与 staging 严格同序；生成列/默认列不参与）。
PROMOTE_TABLE_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "asset_retrieval_units_v2",
        (
            "representation_id", "snapshot_id", "representation_type",
            "content_type", "content_text", "structural_context",
            "lexical_text", "tokenizer_version", "target_type", "target_ref",
            "canonical_evidence_id", "section_ref", "container_ref",
            "parent_ref", "context_group_id", "source_refs_json", "ordinal",
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
         "level", "block_type", "element_id"),
    ),
    (
        "asset_structure_edges",
        ("snapshot_id", "relation", "from_ref", "to_ref"),
    ),
    (
        "asset_structured_assets",
        ("snapshot_id", "asset_ref", "asset_type", "table_ref",
         "columns_json", "row_count", "readiness", "schema_version",
         "sheet_name"),
    ),
    (
        "asset_table_cells",
        ("snapshot_id", "table_ref", "row_index", "column_index",
         "column_name", "value", "is_header", "value_type",
         "normalized_value", "formula", "row_span", "column_span",
         "source_span_id"),
    ),
    (
        "asset_snapshot_readiness",
        ("snapshot_id", "document_ref", "readiness_json", "schema_version",
         "tokenizer_version"),
    ),
    # A1 来源记录面（38 号 §2.1）：随 Build 组装事务统一晋升（挖掘主链）。
    # 受控重放走 PgLocatorStore.promote_locators 专用通道，不经此清单。
    (
        "asset_source_locators",
        ("snapshot_id", "representation_id", "target_ref", "document_ref",
         "source_format", "locator_kind", "section_path", "section_element_id",
         "page", "line_start", "line_end", "sheet", "cell", "table_ref",
         "row_index", "native_ref_json", "description", "locator_version"),
    ),
)

__all__ = [
    "ASSET_SCHEMA_VERSION",
    "PROMOTE_TABLE_COLUMNS",
    "TOKENIZER_VERSION",
]
