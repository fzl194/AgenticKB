-- 阶段 A（批次5）：知识库库级默认检索范式（菜谱+运行时范围分层）。
-- 指向控制库 operator_paradigm（跨库无 FK），应用层校验：
--   PATCH 时须 published active + assemble 终点 + 同域；解析失效时降级域/官方默认并标记 degraded。
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS default_paradigm_id TEXT;
