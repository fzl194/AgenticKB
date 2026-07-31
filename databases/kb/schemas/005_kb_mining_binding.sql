-- KB 挖掘范式绑定：knowledge_bases 增 mining_workflow_id（该 KB 选的挖掘范式）。
-- 软引用 mining_workflows.id（mining_workflows 在 primary/control 库，跨库不建 DB FK），
-- 靠应用层维护引用完整性。NULL = 未选范式（此时 /api/kb/{id}/mine 返回 400）。
-- 换范式不触发自动重挖，由用户在 KB「挖掘」tab 主动触发。
-- 必须在 databases/kb/002_knowledge_bases.sql 之后执行（knowledge_bases 存在）。

ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS mining_workflow_id TEXT;
