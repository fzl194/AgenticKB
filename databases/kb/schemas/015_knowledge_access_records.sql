-- Unified knowledge access ledger. One row represents one user-visible call.
-- Identity columns intentionally have no foreign keys: account/key lifecycle must
-- not erase historical records.
CREATE TABLE IF NOT EXISTS knowledge_access_records (
    id TEXT PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    domain TEXT NOT NULL,
    actor_user_id TEXT,
    actor_username TEXT,
    source TEXT NOT NULL,
    operation TEXT NOT NULL,
    tool_name TEXT,
    mcp_key_id TEXT,
    kb_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    query_text TEXT,
    paradigm_id TEXT,
    paradigm_version INTEGER,
    status TEXT NOT NULL,
    result_count INTEGER,
    duration_ms INTEGER,
    error_code TEXT,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT ck_knowledge_access_source CHECK (source IN ('web', 'mcp', 'api')),
    CONSTRAINT ck_knowledge_access_operation CHECK (operation IN ('search', 'read', 'upload', 'replace')),
    CONSTRAINT ck_knowledge_access_status CHECK (status IN ('pending', 'success', 'no_result', 'denied', 'invalid', 'timeout', 'failed')),
    CONSTRAINT ck_knowledge_access_kb_ids_array CHECK (jsonb_typeof(kb_ids) = 'array'),
    CONSTRAINT ck_knowledge_access_details_object CHECK (jsonb_typeof(details_json) = 'object'),
    CONSTRAINT ck_knowledge_access_result_count CHECK (result_count IS NULL OR result_count >= 0),
    CONSTRAINT ck_knowledge_access_duration CHECK (duration_ms IS NULL OR duration_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_access_domain_time ON knowledge_access_records (domain, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_access_actor_time ON knowledge_access_records (actor_user_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_access_source_tool_time ON knowledge_access_records (source, tool_name, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_access_status_time ON knowledge_access_records (status, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_access_kb_ids ON knowledge_access_records USING GIN (kb_ids);

