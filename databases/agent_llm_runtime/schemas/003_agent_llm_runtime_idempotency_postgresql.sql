-- 003: idempotency dedup lookup index.
--
-- find_existing_task (runtime/idempotency.py) resolves an idempotency_key by
-- probing succeeded, then running, then queued. Without an index each probe
-- is a sequential scan of agent_llm_tasks. Mining's async task channel
-- submits with an idempotency key per batch (dozens per large document),
-- which turns that scan into the hot path. Partial index: keyless tasks
-- (the majority of ad-hoc traffic) stay out of the index.
--
-- NOTE: keep semicolons out of comments -- pg_schema._split_ddl splits on
-- semicolons without comment awareness.

CREATE INDEX IF NOT EXISTS idx_agent_llm_tasks_idempotency_status
    ON agent_llm_tasks(idempotency_key, status, created_at DESC)
    WHERE idempotency_key IS NOT NULL;
