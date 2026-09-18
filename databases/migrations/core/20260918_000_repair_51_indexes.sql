-- 51号跨版本结构漂移修复。
-- 早期批次可能留下 UNIQUE(user_id,name) 约束或同名非唯一 hash 索引；
-- IF NOT EXISTS 无法把它们自动升级成最终定义，因此这里显式收口。

ALTER TABLE mcp_keys DROP CONSTRAINT IF EXISTS mcp_keys_user_id_name_key;

DROP INDEX IF EXISTS idx_mcp_keys_hash;
CREATE UNIQUE INDEX idx_mcp_keys_hash ON mcp_keys(key_hash);

CREATE INDEX IF NOT EXISTS idx_mcp_keys_user ON mcp_keys(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_keys_active_name
    ON mcp_keys(user_id, domain, name) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_user_domains_domain ON user_domains(domain);
