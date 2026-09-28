-- 55号：用户清退与 MCP 吊销记录软隐藏所需的最小在线扩展。
-- 所有列均 nullable；现有用户和钥匙保持可见，迁移不重写业务数据。
ALTER TABLE kb_users
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE kb_users
    ADD COLUMN IF NOT EXISTS deleted_by_user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_kb_users_deleted_at
    ON kb_users (deleted_at) WHERE deleted_at IS NOT NULL;

ALTER TABLE mcp_keys
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_mcp_keys_visible_user
    ON mcp_keys(user_id, created_at) WHERE deleted_at IS NULL;
