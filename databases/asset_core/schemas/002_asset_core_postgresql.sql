-- CoreMasterKB Asset Core Schema v1.1 - PostgreSQL
--
-- Adapted from 001_asset_core.sqlite.sql:
-- - TEXT → TEXT (kept for non-JSON), JSON TEXT → JSONB
-- - FTS5 virtual table → tsvector column + GIN index
-- - PRAGMA removed
-- - Auto-increment triggers replaced by pg_trgm + tsvector

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS asset_source_batches (
    id            TEXT PRIMARY KEY,
    batch_code    TEXT NOT NULL UNIQUE,
    source_type   TEXT NOT NULL CHECK (
        source_type IN (
            'manual_upload',
            'folder_scan',
            'api_import',
            'official_vendor',
            'expert_authored',
            'user_import',
            'synthetic_coldstart',
            'other'
        )
    ),
    domain        TEXT NOT NULL DEFAULT 'default',
    description   TEXT,
    created_by    TEXT,
    created_at    TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);


CREATE TABLE IF NOT EXISTS asset_documents (
    id             TEXT PRIMARY KEY,
    domain         TEXT NOT NULL,
    document_key   TEXT NOT NULL,
    document_name  TEXT,
    document_type  TEXT CHECK (
        document_type IS NULL OR
        document_type IN (
            'command', 'feature', 'procedure', 'troubleshooting', 'alarm',
            'constraint', 'checklist', 'expert_note', 'project_note',
            'standard', 'training', 'reference', 'other'
        )
    ),
    metadata_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TEXT NOT NULL,
    UNIQUE (domain, document_key)
);

CREATE INDEX IF NOT EXISTS idx_asset_documents_type
    ON asset_documents(document_type);

CREATE TABLE IF NOT EXISTS asset_document_snapshots (
    id                      TEXT PRIMARY KEY,
    domain                  TEXT NOT NULL,
    normalized_content_hash TEXT NOT NULL,
    raw_content_hash        TEXT NOT NULL,
    mime_type               TEXT NOT NULL CHECK (
        mime_type IN (
            'text/markdown', 'text/plain', 'text/html', 'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/octet-stream', 'other'
        )
    ),
    title                   TEXT,
    scope_json              JSONB NOT NULL DEFAULT '{}'::jsonb,
    tags_json               JSONB NOT NULL DEFAULT '[]'::jsonb,
    parser_profile_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_json           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TEXT NOT NULL,
    UNIQUE (domain, normalized_content_hash)
);

CREATE INDEX IF NOT EXISTS idx_asset_document_snapshots_raw_hash
    ON asset_document_snapshots(raw_content_hash);

CREATE TABLE IF NOT EXISTS asset_document_snapshot_links (
    id                   TEXT PRIMARY KEY,
    document_id          TEXT NOT NULL REFERENCES asset_documents(id) ON DELETE CASCADE,
    document_snapshot_id TEXT NOT NULL REFERENCES asset_document_snapshots(id) ON DELETE RESTRICT,
    source_batch_id      TEXT REFERENCES asset_source_batches(id) ON DELETE SET NULL,
    relative_path        TEXT NOT NULL,
    source_uri           TEXT NOT NULL,
    title                TEXT,
    scope_json           JSONB NOT NULL DEFAULT '{}'::jsonb,
    tags_json            JSONB NOT NULL DEFAULT '[]'::jsonb,
    linked_at            TEXT NOT NULL,
    metadata_json        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_asset_document_snapshot_links_document
    ON asset_document_snapshot_links(document_id, linked_at);

CREATE INDEX IF NOT EXISTS idx_asset_document_snapshot_links_snapshot
    ON asset_document_snapshot_links(document_snapshot_id, linked_at);

CREATE INDEX IF NOT EXISTS idx_asset_document_snapshot_links_batch
    ON asset_document_snapshot_links(source_batch_id);

CREATE TABLE IF NOT EXISTS asset_raw_segments (
    id                  TEXT PRIMARY KEY,
    document_snapshot_id TEXT NOT NULL REFERENCES asset_document_snapshots(id) ON DELETE CASCADE,
    segment_key         TEXT NOT NULL,
    segment_index       INTEGER NOT NULL CHECK (segment_index >= 0),
    section_path        TEXT NOT NULL DEFAULT '[]',
    section_title       TEXT,
    block_type          TEXT NOT NULL DEFAULT 'unknown' CHECK (
        block_type IN ('paragraph', 'heading', 'table', 'list', 'code', 'blockquote', 'html_table', 'raw_html', 'image', 'unknown')
    ),
    semantic_role       TEXT NOT NULL DEFAULT 'unknown' CHECK (
        semantic_role IN (
            'concept', 'parameter', 'example', 'note', 'procedure_step',
            'troubleshooting_step', 'constraint', 'alarm', 'checklist',
            'definition', 'enumeration', 'conclusion', 'navigation',
            'overview', 'unknown'
        )
    ),
    raw_text            TEXT NOT NULL,
    normalized_text     TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    normalized_hash     TEXT NOT NULL,
    token_count         INTEGER CHECK (token_count IS NULL OR token_count >= 0),
    structure_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_offsets_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    entity_refs_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (document_snapshot_id, segment_key)
);

CREATE INDEX IF NOT EXISTS idx_asset_raw_segments_snapshot
    ON asset_raw_segments(document_snapshot_id);

CREATE INDEX IF NOT EXISTS idx_asset_raw_segments_snapshot_index
    ON asset_raw_segments(document_snapshot_id, segment_index);

CREATE INDEX IF NOT EXISTS idx_asset_raw_segments_normalized_hash
    ON asset_raw_segments(normalized_hash);

CREATE INDEX IF NOT EXISTS idx_asset_raw_segments_block_role
    ON asset_raw_segments(block_type, semantic_role);

CREATE TABLE IF NOT EXISTS asset_builds (
    id               TEXT PRIMARY KEY,
    build_code       TEXT NOT NULL UNIQUE,
    status           TEXT NOT NULL CHECK (
        status IN ('building', 'validated', 'failed', 'published', 'archived')
    ),
    build_mode       TEXT NOT NULL CHECK (build_mode IN ('full', 'incremental')),
    domain           TEXT NOT NULL DEFAULT 'default',
    source_batch_id  TEXT REFERENCES asset_source_batches(id) ON DELETE SET NULL,
    parent_build_id  TEXT REFERENCES asset_builds(id) ON DELETE SET NULL,
    mining_run_id    TEXT,
    summary_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TEXT NOT NULL,
    finished_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_asset_builds_status
    ON asset_builds(status, created_at);

CREATE INDEX IF NOT EXISTS idx_asset_builds_source_batch
    ON asset_builds(source_batch_id);


CREATE TABLE IF NOT EXISTS asset_build_document_snapshots (
    build_id              TEXT NOT NULL REFERENCES asset_builds(id) ON DELETE CASCADE,
    document_id           TEXT NOT NULL REFERENCES asset_documents(id) ON DELETE CASCADE,
    document_snapshot_id  TEXT NOT NULL REFERENCES asset_document_snapshots(id) ON DELETE RESTRICT,
    source_batch_id       TEXT REFERENCES asset_source_batches(id) ON DELETE SET NULL,
    selection_status      TEXT NOT NULL CHECK (selection_status IN ('active', 'removed')),
    reason                TEXT NOT NULL CHECK (reason IN ('add', 'update', 'retain', 'remove')),
    metadata_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (build_id, document_id)
);

ALTER TABLE asset_build_document_snapshots
    ADD COLUMN IF NOT EXISTS source_batch_id TEXT;

CREATE INDEX IF NOT EXISTS idx_asset_build_document_snapshots_snapshot
    ON asset_build_document_snapshots(document_snapshot_id);

CREATE INDEX IF NOT EXISTS idx_asset_build_document_snapshots_batch
    ON asset_build_document_snapshots(source_batch_id);

-- =============================================================================
-- Idempotent upgrade for existing v1 databases (mirrors migrate_v1_to_zdy.sql)
-- These statements are no-ops on a freshly created schema.
-- =============================================================================

ALTER TABLE asset_source_batches
    ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT 'default';

ALTER TABLE asset_builds
    ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_asset_source_batches_domain
    ON asset_source_batches(domain);

CREATE INDEX IF NOT EXISTS idx_asset_builds_domain_status
    ON asset_builds(domain, status);
