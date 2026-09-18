-- =============================================================================
-- Pluggable retrieval operator system — paradigm persistence (WP-6)
-- Lives in the defaultDataSource DB (PG_DBNAME, default test_db) — a shared/control
-- store, NOT a per-domain DB. Paradigm CRUD/reads run with no DomainContext set, so the
-- routing DataSource falls back here; paradigms are domain-agnostic global config.
-- Bootstrap-only DDL. Production startup validates cmkb_schema_migrations and never runs DDL.
-- =============================================================================

-- Paradigm definition (mutable metadata + editable draft)
CREATE TABLE IF NOT EXISTS operator_paradigm (
    id               VARCHAR(64)  PRIMARY KEY,
    name             VARCHAR(200) NOT NULL UNIQUE,
    description      TEXT,
    draft_graph_json JSONB,                          -- editable draft graph (PRD §9 JSON)
    current_version  INT          NOT NULL DEFAULT 0,-- published version in effect (0 = none)
    status           VARCHAR(20)  NOT NULL DEFAULT 'draft',  -- draft | active | archived
    created_at       TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- Paradigm versions (immutable snapshot per publish)
CREATE TABLE IF NOT EXISTS operator_paradigm_version (
    id              VARCHAR(64)  PRIMARY KEY,
    paradigm_id     VARCHAR(64)  NOT NULL,
    version         INT          NOT NULL,            -- 1-based, monotonic per paradigm
    graph_json      JSONB        NOT NULL,            -- immutable compiled-source graph
    schema_version  VARCHAR(20)  NOT NULL DEFAULT '1.0',
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(100),
    CONSTRAINT uq_operator_paradigm_version UNIQUE (paradigm_id, version)
);

CREATE INDEX IF NOT EXISTS idx_operator_paradigm_version
    ON operator_paradigm_version (paradigm_id, version);
