-- KB management: extend asset_documents with KB ownership + file-location columns.
-- KB owns asset_documents identity (single-writer invariant, design §0/§1.2).
-- Must run AFTER databases/kb/002_knowledge_bases.sql (kb_id FK → knowledge_bases)
-- and AFTER 002_asset_core_postgresql.sql (asset_documents exists).

ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS kb_id          TEXT REFERENCES knowledge_bases(id) ON DELETE RESTRICT;  -- M4：禁硬删有文档的 KB
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS storage_path   TEXT;   -- 文件落盘路径
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS directory_path TEXT;   -- KB 内逻辑目录（用户视角）
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS owner_id       TEXT REFERENCES kb_users(id) ON DELETE SET NULL;

-- UNIQUE: (domain, document_key) → (kb_id, document_key)
-- 存量 kb_id NULL，Postgres 多 NULL 不冲突，向后兼容（设计 §1.2）。
ALTER TABLE asset_documents DROP CONSTRAINT IF EXISTS asset_documents_domain_document_key_key;

-- 003_asset_core_domain_isolation 在 004 之后执行，会按 (domain, document_key) 建名约束
-- uq_asset_documents_domain_document_key（legacy 域级身份）。它与 KB 的 (kb_id, document_key)
-- 唯一冲突——挡住「同域多库同 document_key」（即两个 KB 各放一份 qos.pdf），违背「每库独立
-- 文件系统」。这里一并删；003 已加 guard 在 KB 模式（存在 kb_id 唯一）下不再重建。
ALTER TABLE asset_documents DROP CONSTRAINT IF EXISTS uq_asset_documents_domain_document_key;

-- ADD CONSTRAINT 不支持 IF NOT EXISTS，用 DO 块守卫幂等。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_asset_documents_kb_key'
          AND conrelid = 'asset_documents'::regclass
    ) THEN
        ALTER TABLE asset_documents ADD CONSTRAINT uq_asset_documents_kb_key UNIQUE (kb_id, document_key);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_asset_documents_kb_id ON asset_documents(kb_id) WHERE kb_id IS NOT NULL;
