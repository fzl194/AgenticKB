-- P07-S2: DB-owned execution lease for multi-instance run claiming.
ALTER TABLE mining_runs ADD COLUMN IF NOT EXISTS worker_id TEXT;
ALTER TABLE mining_runs ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;
ALTER TABLE mining_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE mining_runs ADD COLUMN IF NOT EXISTS attempt_no INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_mining_runs_lease
    ON mining_runs(status, lease_until) WHERE status IN ('queued', 'running');
