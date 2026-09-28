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
