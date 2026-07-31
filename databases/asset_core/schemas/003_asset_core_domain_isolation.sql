-- Asset Core domain-isolation migration.
-- This file is executed as one transaction by pg_schema.py.

ALTER TABLE asset_documents
    ADD COLUMN IF NOT EXISTS domain TEXT;

ALTER TABLE asset_document_snapshots
    ADD COLUMN IF NOT EXISTS domain TEXT;

ALTER TABLE asset_build_document_snapshots
    ADD COLUMN IF NOT EXISTS source_batch_id TEXT;

-- Remove legacy FKs on the selection provenance column unless they point
-- exactly to asset_source_batches(id) with ON DELETE SET NULL. This must run
-- before backfill so an FK to batch_code cannot reject source-batch IDs.
DO $$
DECLARE
    legacy_fk record;
BEGIN
    FOR legacy_fk IN
        SELECT constraints.conrelid::regclass AS table_name, constraints.conname
        FROM pg_constraint AS constraints
        WHERE constraints.conrelid = 'asset_build_document_snapshots'::regclass
          AND constraints.contype = 'f'
          AND EXISTS (
              SELECT 1
              FROM unnest(constraints.conkey) AS keys(attnum)
              JOIN pg_attribute AS attributes
                ON attributes.attrelid = constraints.conrelid
               AND attributes.attnum = keys.attnum
              WHERE attributes.attname = 'source_batch_id'
          )
          AND NOT (
              constraints.confrelid = 'asset_source_batches'::regclass
              AND constraints.confdeltype = 'n'
              AND cardinality(constraints.conkey) = 1
              AND cardinality(constraints.confkey) = 1
              AND EXISTS (
                  SELECT 1
                  FROM unnest(constraints.confkey) AS keys(attnum)
                  JOIN pg_attribute AS attributes
                    ON attributes.attrelid = constraints.confrelid
                   AND attributes.attnum = keys.attnum
                  WHERE attributes.attname = 'id'
              )
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE %s DROP CONSTRAINT %I',
            legacy_fk.table_name,
            legacy_fk.conname
        );
    END LOOP;
END
$$;

CREATE TEMP TABLE asset_domain_candidates (
    asset_kind TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    domain TEXT NOT NULL
) ON COMMIT DROP;

-- Direct provenance: document/snapshot link -> source batch.
INSERT INTO asset_domain_candidates (asset_kind, asset_id, domain)
SELECT 'document', links.document_id, batches.domain
FROM asset_document_snapshot_links AS links
JOIN asset_source_batches AS batches ON batches.id = links.source_batch_id
WHERE batches.domain IS NOT NULL AND batches.domain NOT IN ('', 'default');

INSERT INTO asset_domain_candidates (asset_kind, asset_id, domain)
SELECT 'snapshot', links.document_snapshot_id, batches.domain
FROM asset_document_snapshot_links AS links
JOIN asset_source_batches AS batches ON batches.id = links.source_batch_id
WHERE batches.domain IS NOT NULL AND batches.domain NOT IN ('', 'default');

-- Build provenance: selected document/snapshot -> build.
INSERT INTO asset_domain_candidates (asset_kind, asset_id, domain)
SELECT 'document', selections.document_id, builds.domain
FROM asset_build_document_snapshots AS selections
JOIN asset_builds AS builds ON builds.id = selections.build_id
WHERE builds.domain IS NOT NULL AND builds.domain NOT IN ('', 'default');

INSERT INTO asset_domain_candidates (asset_kind, asset_id, domain)
SELECT 'snapshot', selections.document_snapshot_id, builds.domain
FROM asset_build_document_snapshots AS selections
JOIN asset_builds AS builds ON builds.id = selections.build_id
WHERE builds.domain IS NOT NULL AND builds.domain NOT IN ('', 'default');

-- Published provenance: selected document/snapshot -> release -> build.
INSERT INTO asset_domain_candidates (asset_kind, asset_id, domain)
SELECT 'document', selections.document_id, releases.domain
FROM asset_publish_releases AS releases
JOIN asset_build_document_snapshots AS selections
  ON selections.build_id = releases.build_id
WHERE releases.domain IS NOT NULL AND releases.domain NOT IN ('', 'default');

INSERT INTO asset_domain_candidates (asset_kind, asset_id, domain)
SELECT 'snapshot', selections.document_snapshot_id, releases.domain
FROM asset_publish_releases AS releases
JOIN asset_build_document_snapshots AS selections
  ON selections.build_id = releases.build_id
WHERE releases.domain IS NOT NULL AND releases.domain NOT IN ('', 'default');

-- Runtime provenance: run document -> run and its source batch. Keep both
-- candidates so inconsistent legacy provenance is classified as shared.
INSERT INTO asset_domain_candidates (asset_kind, asset_id, domain)
SELECT 'document', run_documents.document_id, runs.domain
FROM mining_run_documents AS run_documents
JOIN mining_runs AS runs ON runs.id = run_documents.run_id
WHERE run_documents.document_id IS NOT NULL
  AND runs.domain IS NOT NULL AND runs.domain NOT IN ('', 'default');

INSERT INTO asset_domain_candidates (asset_kind, asset_id, domain)
SELECT 'snapshot', run_documents.document_snapshot_id, runs.domain
FROM mining_run_documents AS run_documents
JOIN mining_runs AS runs ON runs.id = run_documents.run_id
WHERE run_documents.document_snapshot_id IS NOT NULL
  AND runs.domain IS NOT NULL AND runs.domain NOT IN ('', 'default');

INSERT INTO asset_domain_candidates (asset_kind, asset_id, domain)
SELECT 'document', run_documents.document_id, batches.domain
FROM mining_run_documents AS run_documents
JOIN mining_runs AS runs ON runs.id = run_documents.run_id
JOIN asset_source_batches AS batches ON batches.id = runs.source_batch_id
WHERE run_documents.document_id IS NOT NULL
  AND batches.domain IS NOT NULL AND batches.domain NOT IN ('', 'default');

INSERT INTO asset_domain_candidates (asset_kind, asset_id, domain)
SELECT 'snapshot', run_documents.document_snapshot_id, batches.domain
FROM mining_run_documents AS run_documents
JOIN mining_runs AS runs ON runs.id = run_documents.run_id
JOIN asset_source_batches AS batches ON batches.id = runs.source_batch_id
WHERE run_documents.document_snapshot_id IS NOT NULL
  AND batches.domain IS NOT NULL AND batches.domain NOT IN ('', 'default');

WITH resolved AS (
    SELECT documents.id,
           CASE COUNT(DISTINCT candidates.domain)
               WHEN 0 THEN '__legacy_unscoped__'
               WHEN 1 THEN MIN(candidates.domain)
               ELSE '__legacy_shared__'
           END AS domain
    FROM asset_documents AS documents
    LEFT JOIN asset_domain_candidates AS candidates
      ON candidates.asset_kind = 'document' AND candidates.asset_id = documents.id
    WHERE documents.domain IS NULL
    GROUP BY documents.id
)
UPDATE asset_documents AS documents
SET domain = resolved.domain
FROM resolved
WHERE documents.id = resolved.id;

WITH resolved AS (
    SELECT snapshots.id,
           CASE COUNT(DISTINCT candidates.domain)
               WHEN 0 THEN '__legacy_unscoped__'
               WHEN 1 THEN MIN(candidates.domain)
               ELSE '__legacy_shared__'
           END AS domain
    FROM asset_document_snapshots AS snapshots
    LEFT JOIN asset_domain_candidates AS candidates
      ON candidates.asset_kind = 'snapshot' AND candidates.asset_id = snapshots.id
    WHERE snapshots.domain IS NULL
    GROUP BY snapshots.id
)
UPDATE asset_document_snapshots AS snapshots
SET domain = resolved.domain
FROM resolved
WHERE snapshots.id = resolved.id;

-- Backfill a selection only when the most recent eligible link points to one
-- unambiguous source batch in the build's domain.
WITH eligible AS (
    SELECT selections.build_id,
           selections.document_id,
           links.source_batch_id,
           links.linked_at,
           MAX(links.linked_at) OVER (
               PARTITION BY selections.build_id, selections.document_id
           ) AS latest_linked_at
    FROM asset_build_document_snapshots AS selections
    JOIN asset_builds AS builds ON builds.id = selections.build_id
    JOIN asset_document_snapshot_links AS links
      ON links.document_id = selections.document_id
     AND links.document_snapshot_id = selections.document_snapshot_id
    JOIN asset_source_batches AS batches ON batches.id = links.source_batch_id
    WHERE selections.source_batch_id IS NULL
      AND batches.domain = builds.domain
      AND links.linked_at <= builds.created_at
), chosen AS (
    SELECT build_id, document_id, MIN(source_batch_id) AS source_batch_id
    FROM eligible
    WHERE linked_at = latest_linked_at
    GROUP BY build_id, document_id
    HAVING COUNT(DISTINCT source_batch_id) = 1
)
UPDATE asset_build_document_snapshots AS selections
SET source_batch_id = chosen.source_batch_id
FROM chosen
WHERE selections.build_id = chosen.build_id
  AND selections.document_id = chosen.document_id
  AND selections.source_batch_id IS NULL;

-- Remove legacy single-column UNIQUE constraints by their column sets, not
-- by generated names, so both default and custom legacy names are handled.
DO $$
DECLARE
    legacy_constraint record;
BEGIN
    FOR legacy_constraint IN
        SELECT constraints.conrelid::regclass AS table_name, constraints.conname
        FROM pg_constraint AS constraints
        WHERE constraints.connamespace = current_schema()::regnamespace
          AND constraints.contype = 'u'
          AND (
              (
                  constraints.conrelid = 'asset_documents'::regclass
                  AND ARRAY(
                      SELECT attributes.attname
                      FROM unnest(constraints.conkey) AS keys(attnum)
                      JOIN pg_attribute AS attributes
                        ON attributes.attrelid = constraints.conrelid
                       AND attributes.attnum = keys.attnum
                      ORDER BY attributes.attname
                  ) = ARRAY['document_key']::name[]
              )
              OR
              (
                  constraints.conrelid = 'asset_document_snapshots'::regclass
                  AND ARRAY(
                      SELECT attributes.attname
                      FROM unnest(constraints.conkey) AS keys(attnum)
                      JOIN pg_attribute AS attributes
                        ON attributes.attrelid = constraints.conrelid
                       AND attributes.attnum = keys.attnum
                      ORDER BY attributes.attname
                  ) = ARRAY['normalized_content_hash']::name[]
              )
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE %s DROP CONSTRAINT %I',
            legacy_constraint.table_name,
            legacy_constraint.conname
        );
    END LOOP;
END
$$;

DO $$
BEGIN
    -- KB 模式下身份唯一是 (kb_id, document_key)（见 004_kb_isolation）；此时不应再建
    -- (domain, document_key) 域级唯一——否则同域多库无法同名文件。仅在非 KB 模式
    -- （无 kb_id 唯一）时才补建 legacy 域级唯一。
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS constraints
        WHERE constraints.conrelid = 'asset_documents'::regclass
          AND constraints.contype = 'u'
          AND ARRAY(
              SELECT attributes.attname
              FROM unnest(constraints.conkey) AS keys(attnum)
              JOIN pg_attribute AS attributes
                ON attributes.attrelid = constraints.conrelid
               AND attributes.attnum = keys.attnum
              ORDER BY attributes.attname
          ) = ARRAY['document_key', 'domain']::name[]
    ) AND NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS constraints
        WHERE constraints.conrelid = 'asset_documents'::regclass
          AND constraints.contype = 'u'
          AND ARRAY(
              SELECT attributes.attname
              FROM unnest(constraints.conkey) AS keys(attnum)
              JOIN pg_attribute AS attributes
                ON attributes.attrelid = constraints.conrelid
               AND attributes.attnum = keys.attnum
              ORDER BY attributes.attname
          ) = ARRAY['document_key', 'kb_id']::name[]
    ) AND NOT EXISTS (
        -- 存在重复 (domain, document_key) 行时跳过——否则 ADD CONSTRAINT 会撞重复数据失败。
        -- 此时身份唯一交给 KB 模式的 (kb_id, document_key) 或应用层。
        SELECT 1 FROM asset_documents GROUP BY domain, document_key HAVING COUNT(*) > 1
    ) THEN
        ALTER TABLE asset_documents
            ADD CONSTRAINT uq_asset_documents_domain_document_key
            UNIQUE (domain, document_key);
    END IF;

    -- snapshot 的 (domain, normalized_content_hash) 唯一约束【不在这里建】：
    -- master 的 workflow 绑定允许「同内容 + 不同 workflow_graph_hash」的多快照（D-5），
    -- 一个全局 UNIQUE(domain, normalized_content_hash) 会拒绝它，直接破坏该特性。
    -- snapshot 的唯一性完全交给 004_asset_snapshot_workflow_binding 的两个 partial unique：
    --   uq_asset_snapshot_legacy_content   WHERE workflow_graph_hash IS NULL
    --   uq_asset_snapshot_workflow_content WHERE workflow_graph_hash IS NOT NULL
    -- 004 的第一个 DO 块也会按列集 DROP 掉 002 建表时遗留的同形 legacy 约束，无需此处插手。

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS constraints
        WHERE constraints.conrelid = 'asset_build_document_snapshots'::regclass
          AND constraints.contype = 'f'
          AND constraints.confrelid = 'asset_source_batches'::regclass
          AND constraints.confdeltype = 'n'
          AND cardinality(constraints.conkey) = 1
          AND cardinality(constraints.confkey) = 1
          AND EXISTS (
              SELECT 1
              FROM unnest(constraints.conkey) AS keys(attnum)
              JOIN pg_attribute AS attributes
                ON attributes.attrelid = constraints.conrelid
               AND attributes.attnum = keys.attnum
              WHERE attributes.attname = 'source_batch_id'
          )
          AND EXISTS (
              SELECT 1
              FROM unnest(constraints.confkey) AS keys(attnum)
              JOIN pg_attribute AS attributes
                ON attributes.attrelid = constraints.confrelid
               AND attributes.attnum = keys.attnum
              WHERE attributes.attname = 'id'
          )
    ) THEN
        ALTER TABLE asset_build_document_snapshots
            ADD CONSTRAINT fk_asset_build_document_snapshots_source_batch
            FOREIGN KEY (source_batch_id) REFERENCES asset_source_batches(id)
            ON DELETE SET NULL;
    END IF;
END
$$;

ALTER TABLE asset_documents
    ALTER COLUMN domain SET NOT NULL;

ALTER TABLE asset_document_snapshots
    ALTER COLUMN domain SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_asset_documents_domain
    ON asset_documents(domain);

CREATE INDEX IF NOT EXISTS idx_asset_document_snapshots_domain
    ON asset_document_snapshots(domain);

CREATE INDEX IF NOT EXISTS idx_asset_build_document_snapshots_batch
    ON asset_build_document_snapshots(source_batch_id);

-- Replace only restrictive active-release indexes belonging to the target
-- table's schema. A correct composite index is preserved across reruns.
DO $$
DECLARE
    target_table regclass := 'asset_publish_releases'::regclass;
    target_namespace oid;
    obsolete_index record;
    correct_index_exists boolean;
BEGIN
    SELECT relnamespace INTO target_namespace
    FROM pg_class
    WHERE oid = target_table;

    FOR obsolete_index IN
        SELECT index_relations.relname AS index_name,
               index_namespaces.nspname AS schema_name,
               indexes.indisunique,
               indexes.indisvalid,
               ARRAY(
                   SELECT attributes.attname
                   FROM unnest(indexes.indkey::smallint[]) WITH ORDINALITY
                     AS keys(attnum, position)
                   JOIN pg_attribute AS attributes
                     ON attributes.attrelid = indexes.indrelid
                    AND attributes.attnum = keys.attnum
                   WHERE keys.position <= indexes.indnkeyatts
                   ORDER BY keys.position
               ) AS key_columns,
               regexp_replace(
                   lower(pg_get_expr(indexes.indpred, indexes.indrelid)),
                   '[[:space:]()]|::text',
                   '',
                   'g'
               ) AS normalized_predicate
        FROM pg_index AS indexes
        JOIN pg_class AS index_relations ON index_relations.oid = indexes.indexrelid
        JOIN pg_namespace AS index_namespaces
          ON index_namespaces.oid = index_relations.relnamespace
        WHERE indexes.indrelid = target_table
          AND index_relations.relnamespace = target_namespace
    LOOP
        IF (
            obsolete_index.indisunique
            AND obsolete_index.indisvalid
            AND obsolete_index.normalized_predicate = 'status=''active'''
            AND obsolete_index.key_columns IN (
                ARRAY['domain']::name[],
                ARRAY['channel']::name[]
            )
        ) OR (
            obsolete_index.index_name = 'uq_asset_publish_releases_domain_channel_active'
            AND NOT (
                obsolete_index.indisunique
                AND obsolete_index.indisvalid
                AND obsolete_index.normalized_predicate = 'status=''active'''
                AND obsolete_index.key_columns = ARRAY['domain', 'channel']::name[]
            )
        ) THEN
            EXECUTE format(
                'DROP INDEX %I.%I',
                obsolete_index.schema_name,
                obsolete_index.index_name
            );
        END IF;
    END LOOP;

    SELECT EXISTS (
        SELECT 1
        FROM pg_index AS indexes
        JOIN pg_class AS index_relations ON index_relations.oid = indexes.indexrelid
        WHERE indexes.indrelid = target_table
          AND index_relations.relnamespace = target_namespace
          AND indexes.indisunique
          AND indexes.indisvalid
          AND ARRAY(
              SELECT attributes.attname
              FROM unnest(indexes.indkey::smallint[]) WITH ORDINALITY
                AS keys(attnum, position)
              JOIN pg_attribute AS attributes
                ON attributes.attrelid = indexes.indrelid
               AND attributes.attnum = keys.attnum
              WHERE keys.position <= indexes.indnkeyatts
              ORDER BY keys.position
          ) = ARRAY['domain', 'channel']::name[]
          AND regexp_replace(
              lower(pg_get_expr(indexes.indpred, indexes.indrelid)),
              '[[:space:]()]|::text',
              '',
              'g'
          ) = 'status=''active'''
    ) INTO correct_index_exists;

    IF NOT correct_index_exists THEN
        CREATE UNIQUE INDEX uq_asset_publish_releases_domain_channel_active
            ON asset_publish_releases(domain, channel)
            WHERE status = 'active';
    END IF;
END
$$;
