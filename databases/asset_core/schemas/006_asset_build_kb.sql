-- KB 挖掘发布（P2'）：asset_builds 增 kb_id。
-- KB 挖掘走 publish=False（只 build 不 publish 到域级 active release，避免 B1），build 仍产出；
-- 文件详情多 tab 靠「KB 最新 build → asset_build_document_snapshots → snapshot」定位文档当前知识。
-- 回填：存量 build 的 kb_id 从 mining_run_id → mining_runs.metadata_json->>'kb_id' 取。
-- 必须在 002_asset_core_postgresql.sql（asset_builds）与 002_mining_runtime_postgresql.sql（mining_runs）之后。

ALTER TABLE asset_builds ADD COLUMN IF NOT EXISTS kb_id TEXT;

UPDATE asset_builds AS b
SET kb_id = r.metadata_json->>'kb_id'
FROM mining_runs AS r
WHERE b.mining_run_id = r.id
  AND b.kb_id IS NULL
  AND r.metadata_json->>'kb_id' IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_asset_builds_kb ON asset_builds(kb_id) WHERE kb_id IS NOT NULL;
