-- agent_llm_runtime: model_calls 清理扫描索引（2026-09-16 内网几十万行清理超时根因之一）
-- cleanup.py Loop B（DELETE ... WHERE created_at < cutoff LIMIT n）与 dry-run 计数
-- 均按 created_at 扫描；此前只有 type/service_domain 索引，每次批次全表顺序扫。
CREATE INDEX IF NOT EXISTS idx_agent_llm_model_calls_created
    ON agent_llm_model_calls(created_at);
