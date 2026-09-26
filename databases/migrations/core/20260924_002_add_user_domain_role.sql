-- Scope-aware role for one user's membership in one domain.
-- Existing memberships remain ordinary members; site admins stay global in kb_users.
ALTER TABLE user_domains
    ADD COLUMN IF NOT EXISTS domain_role TEXT NOT NULL DEFAULT 'member';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'user_domains'::regclass
           AND conname = 'ck_user_domains_domain_role'
    ) THEN
        ALTER TABLE user_domains
            ADD CONSTRAINT ck_user_domains_domain_role
            CHECK (domain_role IN ('member', 'admin')) NOT VALID;
    END IF;
END
$$;

ALTER TABLE user_domains
    VALIDATE CONSTRAINT ck_user_domains_domain_role;
