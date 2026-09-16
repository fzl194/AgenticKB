-- agent_llm_runtime: partial index for admin retention cleanup
-- Used by llm_service/runtime/cleanup.py batch selection and dry-run counts
-- Only terminal statuses are cleanup-eligible so queued and running rows stay out
-- Note: keep comments semicolon-free (pg_schema _split_ddl splits on semicolons)

CREATE INDEX IF NOT EXISTS idx_agent_llm_tasks_cleanup
    ON agent_llm_tasks(finished_at)
    WHERE status IN ('succeeded', 'failed', 'dead_letter', 'cancelled')
