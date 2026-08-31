-- Retrieval assets v2 + staging schema.
--
-- This migration is deliberately part of service startup/deploy, never a
-- document-processing repository call.  The transaction advisory lock makes
-- concurrent service instances serialize the migration for one database.

SELECT pg_advisory_xact_lock(
    hashtextextended('agentickb:retrieval-assets-v2-staging-schema', 0)
);

CREATE EXTENSION IF NOT EXISTS vector;

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
);

CREATE TABLE IF NOT EXISTS asset_structure_edges (
    snapshot_id TEXT NOT NULL,
    relation    TEXT NOT NULL,
    from_ref    TEXT NOT NULL,
    to_ref      TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, relation, from_ref, to_ref)
);

CREATE TABLE IF NOT EXISTS asset_structured_assets (
    snapshot_id    TEXT NOT NULL,
    asset_ref      TEXT NOT NULL,
    asset_type     TEXT NOT NULL,
    table_ref      TEXT,
    columns_json   JSONB NOT NULL DEFAULT '[]',
    row_count      INTEGER,
    readiness      TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, asset_ref)
);

CREATE TABLE IF NOT EXISTS asset_table_cells (
    snapshot_id  TEXT NOT NULL,
    table_ref    TEXT NOT NULL,
    row_index    INTEGER NOT NULL,
    column_index INTEGER NOT NULL,
    column_name  TEXT NOT NULL,
    value        TEXT NOT NULL,
    is_header    BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (snapshot_id, table_ref, row_index, column_index)
);

CREATE TABLE IF NOT EXISTS asset_retrieval_units_v2 (
    representation_id     TEXT PRIMARY KEY,
    snapshot_id           TEXT NOT NULL,
    representation_type   TEXT NOT NULL,
    content_type          TEXT NOT NULL,
    content_text          TEXT NOT NULL,
    structural_context    TEXT NOT NULL DEFAULT '',
    lexical_text          TEXT,
    target_type           TEXT NOT NULL,
    target_ref            TEXT NOT NULL,
    canonical_evidence_id TEXT NOT NULL,
    container_ref         TEXT,
    parent_ref            TEXT,
    context_group_id      TEXT,
    source_refs_json      JSONB NOT NULL DEFAULT '[]',
    ordinal               INTEGER NOT NULL DEFAULT 0,
    lexical_eligible      BOOLEAN NOT NULL DEFAULT TRUE,
    dense_eligible        BOOLEAN NOT NULL DEFAULT TRUE,
    returnable            BOOLEAN NOT NULL DEFAULT TRUE,
    facets_json           JSONB NOT NULL DEFAULT '{}',
    provenance_json       JSONB NOT NULL DEFAULT '{}',
    tokenizer_version     TEXT,
    search_vector         TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', COALESCE(lexical_text, ''))
    ) STORED
);

ALTER TABLE asset_retrieval_units_v2
    ADD COLUMN IF NOT EXISTS parent_ref TEXT;
ALTER TABLE asset_retrieval_units_v2
    ADD COLUMN IF NOT EXISTS context_group_id TEXT;
ALTER TABLE asset_retrieval_units_v2
    ADD COLUMN IF NOT EXISTS source_refs_json JSONB NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_aru_v2_snapshot
    ON asset_retrieval_units_v2 (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_aru_v2_canonical
    ON asset_retrieval_units_v2 (snapshot_id, canonical_evidence_id);
CREATE INDEX IF NOT EXISTS idx_aru_v2_search
    ON asset_retrieval_units_v2 USING GIN (search_vector);

CREATE TABLE IF NOT EXISTS asset_retrieval_embeddings_v2 (
    embedding_id         TEXT PRIMARY KEY,
    snapshot_id          TEXT NOT NULL,
    representation_id    TEXT NOT NULL,
    strategy             TEXT NOT NULL,
    policy_version       TEXT NOT NULL,
    provider             TEXT NOT NULL,
    model                TEXT NOT NULL,
    model_version        TEXT NOT NULL,
    dimension            INTEGER NOT NULL,
    input_hash           TEXT NOT NULL,
    context_group_hash   TEXT,
    fallback_from        TEXT,
    embedding_vector_vec VECTOR(1024)
);

CREATE INDEX IF NOT EXISTS idx_are_v2_snapshot
    ON asset_retrieval_embeddings_v2 (snapshot_id);

CREATE TABLE IF NOT EXISTS asset_snapshot_readiness (
    snapshot_id       TEXT PRIMARY KEY,
    document_ref      TEXT NOT NULL,
    readiness_json    JSONB NOT NULL,
    schema_version    TEXT NOT NULL,
    tokenizer_version TEXT,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asset_structure_nodes_staging
    (LIKE asset_structure_nodes INCLUDING ALL);
CREATE TABLE IF NOT EXISTS asset_structure_edges_staging
    (LIKE asset_structure_edges INCLUDING ALL);
CREATE TABLE IF NOT EXISTS asset_structured_assets_staging
    (LIKE asset_structured_assets INCLUDING ALL);
CREATE TABLE IF NOT EXISTS asset_table_cells_staging
    (LIKE asset_table_cells INCLUDING ALL);
CREATE TABLE IF NOT EXISTS asset_retrieval_units_v2_staging
    (LIKE asset_retrieval_units_v2 INCLUDING ALL);
CREATE TABLE IF NOT EXISTS asset_retrieval_embeddings_v2_staging
    (LIKE asset_retrieval_embeddings_v2 INCLUDING ALL);
CREATE TABLE IF NOT EXISTS asset_snapshot_readiness_staging
    (LIKE asset_snapshot_readiness INCLUDING ALL);

-- Pre-013 staging tables may already exist.  CREATE TABLE IF NOT EXISTS does
-- not upgrade their shape, so keep staging compatibility explicit as well.
ALTER TABLE asset_retrieval_units_v2_staging
    ADD COLUMN IF NOT EXISTS parent_ref TEXT;
ALTER TABLE asset_retrieval_units_v2_staging
    ADD COLUMN IF NOT EXISTS context_group_id TEXT;
ALTER TABLE asset_retrieval_units_v2_staging
    ADD COLUMN IF NOT EXISTS source_refs_json JSONB NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_asset_structure_nodes_staging_snapshot
    ON asset_structure_nodes_staging (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_asset_structure_edges_staging_snapshot
    ON asset_structure_edges_staging (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_asset_structured_assets_staging_snapshot
    ON asset_structured_assets_staging (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_asset_table_cells_staging_snapshot
    ON asset_table_cells_staging (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_asset_retrieval_units_v2_staging_snapshot
    ON asset_retrieval_units_v2_staging (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_asset_retrieval_embeddings_v2_staging_snapshot
    ON asset_retrieval_embeddings_v2_staging (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_asset_snapshot_readiness_staging_snapshot
    ON asset_snapshot_readiness_staging (snapshot_id);
