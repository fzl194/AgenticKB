-- 收口 visibility 为 private + public 两档(砍 shared)。
--
-- 背景:002 的 CHECK 允许 private/shared/public,但读权限逻辑(db.py is_visible /
-- list_visible)只特判 'public',private 与 shared 行为完全相同 → shared 是死选项。
-- 前端建库/设置已是二元开关(public ⇄ private),只产出这两值。
--
-- 本文件:① 历史 shared 行降级为 private(语义最接近:都是受限访问);
--         ② 收紧 CHECK 为 ('private','public')。
--
-- 幂等:三条语句均可重复执行。
--   - UPDATE:无 shared 行时影响 0 行;
--   - DROP CONSTRAINT IF EXISTS:不存在即跳过;
--   - ADD CONSTRAINT:重名时 PG 抛 duplicate_object(42710),pg_schema._execute_ddl
--     非事务分支吞 DuplicateObject,等价幂等。
-- 必须在 002_knowledge_bases.sql 之后(ALTER 依赖基表已建)。
UPDATE knowledge_bases SET visibility='private' WHERE visibility='shared';
ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS knowledge_bases_visibility_check;
ALTER TABLE knowledge_bases ADD CONSTRAINT knowledge_bases_visibility_check
      CHECK (visibility IN ('private','public'));
