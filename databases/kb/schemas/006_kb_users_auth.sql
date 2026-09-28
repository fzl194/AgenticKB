-- Phase 2 用户权限管理：kb_users 增加登录凭证与站点级角色。
-- password_hash 为 NULL 表示不可登录（Phase 1 仅被 X-KB-User upsert 出来的行）。
-- site_role 现有行默认 'member'；首 admin 由 mining 启动期 bootstrap 播种提权。
ALTER TABLE kb_users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE kb_users ADD COLUMN IF NOT EXISTS site_role TEXT NOT NULL DEFAULT 'member'
                  CHECK (site_role IN ('admin','member'));

-- 55号：账号清退沿用 status='disabled'，deleted_at 区分可恢复禁用与永久清退。
-- deleted_by_user_id 仅作审计软引用，避免用户生命周期形成循环 FK。
ALTER TABLE kb_users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE kb_users ADD COLUMN IF NOT EXISTS deleted_by_user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_kb_users_deleted_at
    ON kb_users (deleted_at) WHERE deleted_at IS NOT NULL;
