-- 阶段 A（批次5）：用户级 MCP 接入——一人一钥 + 开放库清单。
-- mcp_access 每用户至多一行（PK=user_id），rotate 覆盖 key_hash 即轮换（旧钥立即失效，无并存期）。
-- 明文密钥永不入库：只存 sha256 hex；key_prefix 为明文前 8 位供界面识别。
-- mcp_open_kbs：用户 MCP 开放的库（∩ 实时权限后才生效；权限收窄即时收窄）。
CREATE TABLE IF NOT EXISTS mcp_access (
  user_id       TEXT PRIMARY KEY REFERENCES kb_users(id) ON DELETE CASCADE,
  key_hash      TEXT NOT NULL,
  key_prefix    TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at  TIMESTAMPTZ,
  rotated_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS mcp_open_kbs (
  user_id       TEXT NOT NULL REFERENCES kb_users(id) ON DELETE CASCADE,
  kb_id         TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, kb_id)
);
