-- KB management: users (Phase 1 auth = header-injected username).
-- Style aligned with asset_core: TEXT PK / TEXT timestamps / JSONB.
CREATE TABLE IF NOT EXISTS kb_users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,          -- 登录名 / 控制面头部注入名
    display_name  TEXT,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_at    TEXT NOT NULL
);
