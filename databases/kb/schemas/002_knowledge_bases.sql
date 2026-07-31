-- KB management: knowledge bases (user-facing document container).
-- Belongs to a domain (must be a valid domain in domain_registry.yaml, app-layer validated).
-- Soft delete: status='deleted' + deleted_at; row kept; asset_documents.kb_id preserved.
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id            TEXT PRIMARY KEY,
    domain        TEXT NOT NULL,                 -- 必须是 domain_registry.yaml 合法域（应用层校验）
    name          TEXT NOT NULL,
    description   TEXT,
    owner_id      TEXT NOT NULL REFERENCES kb_users(id),
    visibility    TEXT NOT NULL DEFAULT 'private'
                  CHECK (visibility IN ('private','shared','public')),
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','deleted')),  -- 软删（设计 M2/M4）
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    deleted_at    TEXT,                          -- 软删时间戳；status='deleted' 时非空
    UNIQUE (domain, name)                        -- 域内 KB 名唯一
);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_domain ON knowledge_bases(domain) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_owner ON knowledge_bases(owner_id);
