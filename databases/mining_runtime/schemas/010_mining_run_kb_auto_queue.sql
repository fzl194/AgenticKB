-- 同库自动挖掘排队（MCP 上传自动触发）：queued 不再占用唯一性槽位。
-- 背景：自动触发采用排队语义——库在 running/审核中/待恢复期间，新 Run 允许先排队
-- （由域级 FIFO 串行执行 + 调度器活跃守卫保证同库互斥），不再以 409 拒绝。
-- 收窄方向永远是安全的：满足旧约束（≤1 个 open）的任何存量状态都满足新约束。

SELECT pg_advisory_xact_lock(
    hashtextextended('agentickb:mining-run-kb-queue-v2', 0)
);

DROP INDEX IF EXISTS uq_mining_runs_one_open_per_kb;

CREATE UNIQUE INDEX IF NOT EXISTS uq_mining_runs_one_active_per_kb
    ON mining_runs (kb_id)
    WHERE kb_id IS NOT NULL
      AND status IN ('running', 'awaiting_review', 'interrupted');
