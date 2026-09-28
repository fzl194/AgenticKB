-- One-time cutover from the incomplete Java query log to the unified ledger.
-- Historical serving_query_logs rows are intentionally not copied.
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
    CONSTRAINT ck_knowledge_access_operation CHECK (operation IN ('search', 'read', 'upload')),
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

-- Heavy request/response snapshots for the unified access ledger.
-- Kept one-to-one and out of list/summary queries; detail reads load it on demand.
CREATE TABLE IF NOT EXISTS knowledge_access_record_payloads (
    record_id TEXT PRIMARY KEY REFERENCES knowledge_access_records(id) ON DELETE CASCADE,
    request_json JSONB,
    effective_context_json JSONB,
    response_mode TEXT NOT NULL,
    response_json JSONB,
    response_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    request_bytes INTEGER,
    response_bytes INTEGER,
    response_truncated BOOLEAN NOT NULL DEFAULT false,
    response_original_bytes INTEGER,
    response_omitted_count INTEGER NOT NULL DEFAULT 0,
    response_sha256 TEXT,
    redactions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload_schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_knowledge_access_payload_mode CHECK (response_mode IN ('snapshot', 'reference', 'summary')),
    CONSTRAINT ck_knowledge_access_payload_refs CHECK (jsonb_typeof(response_refs_json) = 'array'),
    CONSTRAINT ck_knowledge_access_payload_redactions CHECK (jsonb_typeof(redactions_json) = 'array'),
    CONSTRAINT ck_knowledge_access_payload_request_bytes CHECK (request_bytes IS NULL OR request_bytes >= 0),
    CONSTRAINT ck_knowledge_access_payload_response_bytes CHECK (response_bytes IS NULL OR response_bytes >= 0),
    CONSTRAINT ck_knowledge_access_payload_original_bytes CHECK (response_original_bytes IS NULL OR response_original_bytes >= 0),
    CONSTRAINT ck_knowledge_access_payload_omitted CHECK (response_omitted_count >= 0),
    CONSTRAINT ck_knowledge_access_payload_schema_version CHECK (payload_schema_version > 0),
    CONSTRAINT ck_knowledge_access_payload_hash CHECK (response_sha256 IS NULL OR response_sha256 ~ '^[0-9a-f]{64}$')
);

DROP TABLE IF EXISTS serving_query_logs RESTRICT;

