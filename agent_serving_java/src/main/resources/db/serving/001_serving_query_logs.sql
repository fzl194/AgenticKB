-- =============================================================================
-- Serving runtime — query log table.
-- Lives in EVERY database serving can route to: the mappers go through the
-- @Primary DomainRoutingDataSource, so a domain with its own inline `database:`
-- block writes its logs into that domain's DB, not the default one. Applied by
-- ServingRuntimeSchemaInitializer on startup (default DS) and on each pool
-- creation in DomainPoolManager. Idempotent.
--
-- Columns must stay in sync with mapper/ServingQueryLogMapper.xml — nothing
-- else validates the contract.
-- =============================================================================

CREATE TABLE IF NOT EXISTS serving_query_logs (
    id                  TEXT    NOT NULL,

    -- Original input
    query_text          TEXT    NOT NULL,
    domain              TEXT    NOT NULL DEFAULT 'default',
    channel             TEXT    NOT NULL,

    -- Query analysis results (from NormalizedQuery)
    intent              TEXT,
    normalizer_source   TEXT,
    keywords_json       TEXT    NOT NULL DEFAULT '[]',
    entities_json       TEXT    NOT NULL DEFAULT '[]',
    scope_json          TEXT    NOT NULL DEFAULT '{}',

    -- Knowledge base version (from ActiveScope)
    release_id          TEXT,
    build_id            TEXT,
    snapshot_count      INTEGER,

    -- Response summary (from ContextPack)
    result_item_count   INTEGER,
    result_seed_count   INTEGER,
    result_has_result   BOOLEAN NOT NULL DEFAULT TRUE,
    result_issues_json  TEXT    NOT NULL DEFAULT '[]',

    -- Result details (no text body to keep row size small)
    result_items_json       TEXT    NOT NULL DEFAULT '[]',
    result_sources_json     TEXT    NOT NULL DEFAULT '[]',
    result_relations_json   TEXT    NOT NULL DEFAULT '[]',

    -- Performance
    duration_ms         INTEGER,

    -- Metadata
    queried_at          TEXT    NOT NULL,
    metadata_json       TEXT    NOT NULL DEFAULT '{}',

    CONSTRAINT pk_serving_query_logs PRIMARY KEY (id)
);

-- CREATE TABLE IF NOT EXISTS matches on the table NAME only — on a database that
-- already carries an older shape of this table it is skipped silently and the
-- missing column is never added. `domain` was added after the table shipped
-- Older installations may still have a same-named non-vector column, so repair it
-- explicitly. Must run AFTER
-- the CREATE (ALTER on a nonexistent table would abort the script) and BEFORE
-- any index that references the column.
ALTER TABLE serving_query_logs
    ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT 'default';

-- ---- serving_query_logs indexes ----
CREATE INDEX IF NOT EXISTS idx_sql_queried_at
    ON serving_query_logs (queried_at);

CREATE INDEX IF NOT EXISTS idx_sql_intent
    ON serving_query_logs (intent, queried_at);

CREATE INDEX IF NOT EXISTS idx_sql_channel_release
    ON serving_query_logs (channel, release_id);

CREATE INDEX IF NOT EXISTS idx_sql_has_result
    ON serving_query_logs (result_has_result, queried_at);
