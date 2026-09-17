-- 51号批次2：MCP 多钥匙化——一人多把「单域钥匙」。
-- mcp_keys 每行一把钥匙：绑定唯一 domain；010 三列语义随行迁移（open_tools NULL=全开）。
-- 旧 mcp_access/mcp_open_kbs 保留一个周期（迁移源+回滚），批次3 删除。
CREATE TABLE IF NOT EXISTS mcp_keys (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES kb_users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    domain      TEXT NOT NULL,            -- registry 合法域，应用层校验（同 knowledge_bases.domain 风格）
    key_hash    TEXT NOT NULL,            -- sha256，只存哈希
    key_prefix  TEXT NOT NULL,            -- 明文前 8 位，界面识别
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    open_tools  JSONB,                    -- NULL=三件套全开
    instructions TEXT,
    tool_descriptions JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at  TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,             -- 验钥时节流更新（沿用旧 mcp_access 语义）
    UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_mcp_keys_user ON mcp_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_keys_hash ON mcp_keys(key_hash);

-- 钥匙级开放库：入库时应用层校验 kb.domain = key.domain（INSERT...SELECT 兜底）。
CREATE TABLE IF NOT EXISTS mcp_key_open_kbs (
    key_id     TEXT NOT NULL REFERENCES mcp_keys(id) ON DELETE CASCADE,
    kb_id      TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key_id, kb_id)
);
