-- 51号设计批次1：用户↔domain 绑定表。
-- 可见性判定（public 域内化）与建库权限收敛的数据基础。
-- domain 为 domain_registry.yaml 合法域（应用层校验，同 knowledge_bases.domain 风格）。
-- user_id 有意不加 FK：kb_users 走 status 软禁用无硬删路径，孤儿行可容忍（51号裁定）。
CREATE TABLE IF NOT EXISTS user_domains (
    user_id    TEXT NOT NULL,
    domain     TEXT NOT NULL,
    domain_role TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, domain),
    CONSTRAINT ck_user_domains_domain_role CHECK (domain_role IN ('member', 'admin'))
);

-- 兼容 1.1.9/1.1.10 已有表：常量默认列在 PostgreSQL 11+ 为元数据级扩展，
-- 不重写 user_domains 历史行。约束独立补齐，保证重复执行幂等。
ALTER TABLE user_domains
    ADD COLUMN IF NOT EXISTS domain_role TEXT NOT NULL DEFAULT 'member';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'user_domains'::regclass
          AND conname = 'ck_user_domains_domain_role'
    ) THEN
        ALTER TABLE user_domains
            ADD CONSTRAINT ck_user_domains_domain_role
            CHECK (domain_role IN ('member', 'admin')) NOT VALID;
    END IF;
END $$;

ALTER TABLE user_domains
    VALIDATE CONSTRAINT ck_user_domains_domain_role;

-- 删域保护 / 按域反查绑定用户时会用到
CREATE INDEX IF NOT EXISTS idx_user_domains_domain ON user_domains (domain);
