-- 源：databases/kb/schemas/013_user_domains.sql，改表请双向同步。
-- 51号设计批次1：用户↔domain 绑定表。
-- 可见性判定（public 域内化）与建库权限收敛的数据基础。
-- domain 为 domain_registry.yaml 合法域（应用层校验，同 knowledge_bases.domain 风格）。
-- user_id 有意不加 FK：kb_users 走 status 软禁用无硬删路径，孤儿行可容忍（51号裁定）。
CREATE TABLE IF NOT EXISTS user_domains (
    user_id    TEXT NOT NULL,
    domain     TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, domain)
);

-- 删域保护 / 按域反查绑定用户时会用到
CREATE INDEX IF NOT EXISTS idx_user_domains_domain ON user_domains (domain);
