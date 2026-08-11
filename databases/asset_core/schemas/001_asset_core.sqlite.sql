-- CoreMasterKB Asset Core Schema v1.1 - SQLite
--
-- Current policy:
-- 1. asset_core stores stable content assets plus build/release control data.
-- 2. Shared immutable content snapshots are the content reuse boundary.
-- 3. Build selects document -> snapshot mappings.
-- 4. Release publishes one build to one channel.
-- 5. Retrieval path: release -> build -> selected snapshots -> retrieval_units -> source_refs -> raw_segments/relations.

PRAGMA foreign_keys = ON;

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
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS asset_documents (
    id             TEXT PRIMARY KEY,
    domain         TEXT NOT NULL,
    document_key   TEXT NOT NULL,
    document_name  TEXT,
    document_type  TEXT CHECK (
        document_type IS NULL OR
        document_type IN (
            'command',
            'feature',
            'procedure',
            'troubleshooting',
            'alarm',
            'constraint',
            'checklist',
            'expert_note',
            'project_note',
            'standard',
            'training',
            'reference',
            'other'
        )
    ),
    metadata_json  TEXT NOT NULL DEFAULT '{}',
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
            'text/markdown',
            'text/plain',
            'text/html',
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/octet-stream',
            'other'
        )
    ),
    title                   TEXT,
    scope_json              TEXT NOT NULL DEFAULT '{}',
    tags_json               TEXT NOT NULL DEFAULT '[]',
    parser_profile_json     TEXT NOT NULL DEFAULT '{}',
    metadata_json           TEXT NOT NULL DEFAULT '{}',
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
    scope_json           TEXT NOT NULL DEFAULT '{}',
    tags_json            TEXT NOT NULL DEFAULT '[]',
    linked_at            TEXT NOT NULL,
    metadata_json        TEXT NOT NULL DEFAULT '{}'
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
            'concept',
            'parameter',
            'example',
            'note',
            'procedure_step',
            'troubleshooting_step',
            'constraint',
            'alarm',
            'checklist',
            'unknown'
        )
    ),
    raw_text            TEXT NOT NULL,
    normalized_text     TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    normalized_hash     TEXT NOT NULL,
    token_count         INTEGER CHECK (token_count IS NULL OR token_count >= 0),
    structure_json      TEXT NOT NULL DEFAULT '{}',
    source_offsets_json TEXT NOT NULL DEFAULT '{}',
    entity_refs_json    TEXT NOT NULL DEFAULT '[]',
    metadata_json       TEXT NOT NULL DEFAULT '{}',
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

CREATE TABLE IF NOT EXISTS asset_raw_segment_relations (
    id                  TEXT PRIMARY KEY,
    document_snapshot_id TEXT NOT NULL REFERENCES asset_document_snapshots(id) ON DELETE CASCADE,
    source_segment_id   TEXT NOT NULL REFERENCES asset_raw_segments(id) ON DELETE CASCADE,
    target_segment_id   TEXT NOT NULL REFERENCES asset_raw_segments(id) ON DELETE CASCADE,
    relation_type       TEXT NOT NULL CHECK (
        relation_type IN (
            'previous',
            'next',
            'same_section',
            'same_parent_section',
            'section_header_of',
            'references',
            'elaborates',
            'condition',
            'contrast',
            'evidences',
            'causes',
            'results_in',
            'backgrounds',
            'conditions',
            'summarizes',
            'justifies',
            'enables',
            'contrasts_with',
            'parallels',
            'sequences',
            'unrelated',
            'other'
        )
    ),
    weight              REAL NOT NULL DEFAULT 1.0,
    confidence          REAL NOT NULL DEFAULT 1.0,
    distance            INTEGER,
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source_segment_id, target_segment_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_asset_raw_segment_relations_snapshot
    ON asset_raw_segment_relations(document_snapshot_id, relation_type);

CREATE INDEX IF NOT EXISTS idx_asset_raw_segment_relations_source
    ON asset_raw_segment_relations(source_segment_id, relation_type);

CREATE INDEX IF NOT EXISTS idx_asset_raw_segment_relations_target
    ON asset_raw_segment_relations(target_segment_id, relation_type);

CREATE TABLE IF NOT EXISTS asset_retrieval_units (
    id                   TEXT PRIMARY KEY,
    document_snapshot_id TEXT NOT NULL REFERENCES asset_document_snapshots(id) ON DELETE CASCADE,
    unit_key             TEXT NOT NULL,
    unit_type            TEXT NOT NULL CHECK (
        unit_type IN (
            'raw_text',
            'contextual_text',
            'summary',
            'generated_question',
            'entity_card',
            'table_row',
            'other'
        )
    ),
    target_type          TEXT NOT NULL CHECK (
        target_type IN ('raw_segment', 'section', 'document', 'entity', 'synthetic', 'other')
    ),
    target_ref_json      TEXT NOT NULL DEFAULT '{}',
    title                TEXT,
    text                 TEXT NOT NULL,
    search_text          TEXT NOT NULL,
    block_type           TEXT NOT NULL DEFAULT 'unknown' CHECK (
        block_type IN ('paragraph', 'heading', 'table', 'list', 'code', 'blockquote', 'html_table', 'raw_html', 'image', 'unknown')
    ),
    semantic_role        TEXT NOT NULL DEFAULT 'unknown' CHECK (
        semantic_role IN (
            'concept',
            'parameter',
            'example',
            'note',
            'procedure_step',
            'troubleshooting_step',
            'constraint',
            'alarm',
            'checklist',
            'unknown'
        )
    ),
    facets_json          TEXT NOT NULL DEFAULT '{}',
    entity_refs_json     TEXT NOT NULL DEFAULT '[]',
    source_refs_json     TEXT NOT NULL DEFAULT '{}',
    llm_result_refs_json TEXT NOT NULL DEFAULT '{}',
    source_segment_id    TEXT REFERENCES asset_raw_segments(id) ON DELETE SET NULL,
    weight               REAL NOT NULL DEFAULT 1.0,
    created_at           TEXT NOT NULL,
    metadata_json        TEXT NOT NULL DEFAULT '{}',
    UNIQUE (document_snapshot_id, unit_key)
);

CREATE INDEX IF NOT EXISTS idx_asset_retrieval_units_snapshot
    ON asset_retrieval_units(document_snapshot_id);

CREATE INDEX IF NOT EXISTS idx_asset_retrieval_units_unit_type
    ON asset_retrieval_units(unit_type);

CREATE INDEX IF NOT EXISTS idx_asset_retrieval_units_block_role
    ON asset_retrieval_units(block_type, semantic_role);

CREATE INDEX IF NOT EXISTS idx_asset_retrieval_units_source_segment
    ON asset_retrieval_units(source_segment_id);

CREATE VIRTUAL TABLE IF NOT EXISTS asset_retrieval_units_fts
USING fts5(
    retrieval_unit_id UNINDEXED,
    title,
    text,
    search_text,
    tokenize = 'unicode61'
);

CREATE TRIGGER IF NOT EXISTS trg_asset_retrieval_units_ai
AFTER INSERT ON asset_retrieval_units
BEGIN
    INSERT INTO asset_retrieval_units_fts (retrieval_unit_id, title, text, search_text)
    VALUES (new.id, coalesce(new.title, ''), new.text, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS trg_asset_retrieval_units_au
AFTER UPDATE ON asset_retrieval_units
BEGIN
    DELETE FROM asset_retrieval_units_fts WHERE retrieval_unit_id = old.id;
    INSERT INTO asset_retrieval_units_fts (retrieval_unit_id, title, text, search_text)
    VALUES (new.id, coalesce(new.title, ''), new.text, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS trg_asset_retrieval_units_ad
AFTER DELETE ON asset_retrieval_units
BEGIN
    DELETE FROM asset_retrieval_units_fts WHERE retrieval_unit_id = old.id;
END;

CREATE TABLE IF NOT EXISTS asset_retrieval_embeddings (
    id                 TEXT PRIMARY KEY,
    retrieval_unit_id  TEXT NOT NULL REFERENCES asset_retrieval_units(id) ON DELETE CASCADE,
    embedding_model    TEXT NOT NULL,
    embedding_provider TEXT NOT NULL,
    text_kind          TEXT NOT NULL,
    embedding_dim      INTEGER NOT NULL CHECK (embedding_dim > 0),
    embedding_vector   TEXT NOT NULL,
    content_hash       TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    metadata_json      TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_asset_retrieval_embeddings_unit
    ON asset_retrieval_embeddings(retrieval_unit_id);

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
    summary_json     TEXT NOT NULL DEFAULT '{}',
    validation_json  TEXT NOT NULL DEFAULT '{}',
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
    metadata_json         TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (build_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_build_document_snapshots_snapshot
    ON asset_build_document_snapshots(document_snapshot_id);

CREATE INDEX IF NOT EXISTS idx_asset_build_document_snapshots_batch
    ON asset_build_document_snapshots(source_batch_id);

CREATE TABLE IF NOT EXISTS asset_publish_releases (
    id                   TEXT PRIMARY KEY,
    release_code         TEXT NOT NULL UNIQUE,
    build_id             TEXT NOT NULL REFERENCES asset_builds(id) ON DELETE RESTRICT,
    domain               TEXT NOT NULL DEFAULT 'default',
    channel              TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('staging', 'active', 'retired', 'failed')),
    previous_release_id  TEXT REFERENCES asset_publish_releases(id) ON DELETE SET NULL,
    released_by          TEXT,
    release_notes        TEXT,
    activated_at         TEXT,
    deactivated_at       TEXT,
    metadata_json        TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_publish_releases_domain_channel_active
    ON asset_publish_releases(domain, channel)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_asset_publish_releases_build
    ON asset_publish_releases(build_id);

CREATE INDEX IF NOT EXISTS idx_asset_publish_releases_channel_status
    ON asset_publish_releases(channel, status);
