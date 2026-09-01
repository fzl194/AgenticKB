-- One open mining Run per KB while different KBs may wait in the domain FIFO.

SELECT pg_advisory_xact_lock(
    hashtextextended('agentickb:mining-run-kb-queue-v1', 0)
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM mining_runs
        WHERE kb_id IS NOT NULL
          AND status IN ('queued', 'running', 'awaiting_review', 'interrupted')
        GROUP BY kb_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'multiple open mining runs exist for one KB; resolve them before queue migration';
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_mining_runs_one_open_per_kb
    ON mining_runs (kb_id)
    WHERE kb_id IS NOT NULL
      AND status IN ('queued', 'running', 'awaiting_review', 'interrupted');

CREATE INDEX IF NOT EXISTS idx_mining_runs_domain_queue
    ON mining_runs (domain, started_at, id)
    WHERE status IN ('queued', 'interrupted');
