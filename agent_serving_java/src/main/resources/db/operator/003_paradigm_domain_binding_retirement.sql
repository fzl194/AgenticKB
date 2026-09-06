-- 003：范式域绑定退役前向迁移（代码瘦身批次5 + 发布复审 P1）
--
-- 002 曾经创建部分唯一索引 uq_paradigm_domain_default 与 bound_domain/is_default/
-- bound_at 三列。绑定语义退役后代码已不再读写这些列，但存量升级库中的索引若残留，
-- 任何来源（手工 SQL、旧备份恢复）写入的 is_default 行都可能让范式 publish/rollback
-- 以 23505 失败且无恢复手段。本迁移幂等移除索引与列（当前各库 is_default 数据为零，
-- 无数据损失）；新库只会执行 001+003，不再创建绑定列。

DROP INDEX IF EXISTS uq_paradigm_domain_default;

ALTER TABLE operator_paradigm DROP COLUMN IF EXISTS bound_domain;
ALTER TABLE operator_paradigm DROP COLUMN IF EXISTS is_default;
ALTER TABLE operator_paradigm DROP COLUMN IF EXISTS bound_at;
