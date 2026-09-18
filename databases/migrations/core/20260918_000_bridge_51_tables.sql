-- 52 one-hop compatibility: materialize design-51 tables for databases that
-- have not deployed 51 yet. Data backfill is performed by bridge_51.py before
-- this migration is recorded; these idempotent definitions pin the structure.

ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS knowledge_bases_status_check;
ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS ck_knowledge_bases_status;
ALTER TABLE knowledge_bases
    ADD CONSTRAINT ck_knowledge_bases_status
    CHECK (status IN ('active', 'deleting', 'deleted'));

CREATE TABLE IF NOT EXISTS kb_purge_tasks (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL,
    kb_name TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    phase TEXT NOT NULL DEFAULT 'queued',
    progress_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    requested_by TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_purge_tasks_domain
    ON kb_purge_tasks(domain, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_purge_tasks_kb ON kb_purge_tasks(kb_id);

CREATE TABLE IF NOT EXISTS user_domains (
    user_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, domain)
);

CREATE TABLE IF NOT EXISTS mcp_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES kb_users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    open_tools JSONB,
    instructions TEXT,
    tool_descriptions JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS mcp_key_open_kbs (
    key_id TEXT NOT NULL REFERENCES mcp_keys(id) ON DELETE CASCADE,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key_id, kb_id)
);
