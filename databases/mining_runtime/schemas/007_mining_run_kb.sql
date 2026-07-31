-- KB 挖掘记录归属：mining_runs 增 kb_id（从 metadata_json 提升为列，便于按库查挖掘记录）。
-- 幂等 + 回填存量。必须 在 002_mining_runtime_postgresql.sql（mining_runs 存在）之后。

ALTER TABLE mining_runs ADD COLUMN IF NOT EXISTS kb_id TEXT;

UPDATE mining_runs
SET kb_id = metadata_json->>'kb_id'
WHERE kb_id IS NULL
  AND metadata_json->>'kb_id' IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_mining_runs_kb ON mining_runs(kb_id) WHERE kb_id IS NOT NULL;
