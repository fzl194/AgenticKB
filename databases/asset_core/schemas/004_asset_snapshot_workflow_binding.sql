-- A content-equivalent file may have isolated snapshots produced by different
-- immutable Workflow releases. Legacy snapshots keep a NULL Workflow binding.

ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS workflow_id TEXT;
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS workflow_version INTEGER;
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS workflow_version_id TEXT;
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS workflow_graph_hash TEXT;

-- Drop the old domain + content uniqueness constraint by its column set. Its
-- generated name is not stable across upgraded installations.
DO $$
DECLARE
    old_constraint record;
BEGIN
    FOR old_constraint IN
        SELECT constraints.conname
        FROM pg_constraint AS constraints
        WHERE constraints.conrelid = 'asset_document_snapshots'::regclass
          AND constraints.contype = 'u'
          AND ARRAY(
              SELECT attributes.attname
              FROM unnest(constraints.conkey) AS keys(attnum)
              JOIN pg_attribute AS attributes
                ON attributes.attrelid = constraints.conrelid
               AND attributes.attnum = keys.attnum
              ORDER BY attributes.attname
          ) = ARRAY['domain', 'normalized_content_hash']::name[]
    LOOP
        EXECUTE format(
            'ALTER TABLE asset_document_snapshots DROP CONSTRAINT %I',
            old_constraint.conname
        );
    END LOOP;
END
$$;

DO $$
BEGIN
    ALTER TABLE asset_document_snapshots
        ADD CONSTRAINT ck_asset_snapshot_workflow_binding_complete
        CHECK (
            (
                workflow_id IS NULL
                AND workflow_version IS NULL
                AND workflow_version_id IS NULL
                AND workflow_graph_hash IS NULL
            )
            OR
            (
                workflow_id IS NOT NULL
                AND workflow_version IS NOT NULL
                AND workflow_version > 0
                AND workflow_version_id IS NOT NULL
                AND workflow_graph_hash IS NOT NULL
            )
        );
EXCEPTION WHEN duplicate_object THEN
    NULL;
END
$$;

-- partial unique 仅在无重复数据时创建——CREATE UNIQUE INDEX 的 IF NOT EXISTS 只查索引是否存在，
-- 不查数据是否干净；存量重复行（legacy 或 workflow）会让建索引撞 unique_violation 而整个 schema
-- 初始化失败。有重复时跳过（系统仍可运行，应用层 upsert 仍去重），待清理数据后再强约束。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM asset_document_snapshots
        WHERE workflow_graph_hash IS NULL
        GROUP BY domain, normalized_content_hash HAVING COUNT(*) > 1
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_snapshot_legacy_content
            ON asset_document_snapshots(domain, normalized_content_hash)
            WHERE workflow_graph_hash IS NULL;
    ELSE
        RAISE NOTICE 'Skip uq_asset_snapshot_legacy_content: duplicate legacy (domain, normalized_content_hash) rows exist.';
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM asset_document_snapshots
        WHERE workflow_graph_hash IS NOT NULL
        GROUP BY domain, normalized_content_hash, workflow_id, workflow_version, workflow_graph_hash
        HAVING COUNT(*) > 1
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_snapshot_workflow_content
            ON asset_document_snapshots(
                domain,
                normalized_content_hash,
                workflow_id,
                workflow_version,
                workflow_graph_hash
            )
            WHERE workflow_graph_hash IS NOT NULL;
    ELSE
        RAISE NOTICE 'Skip uq_asset_snapshot_workflow_content: duplicate workflow rows exist.';
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_asset_snapshot_raw_workflow
    ON asset_document_snapshots(domain, raw_content_hash, workflow_graph_hash);
