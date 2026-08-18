-- =============================================================================
-- 010_m4_parse_run_state_machine_postgresql.sql — M4 Parse Run 状态机扩列（PG）
-- =============================================================================
-- 文档解析平台化 M4（SRS §9.2 完整 Parse Run 状态机 + §9.5 SUPERSEDED +
-- §4.6 attempt 事件）。ADR-0003 D-003 / D-004（增量幂等，不改读写）。
-- 对齐 010_m4_parse_run_state_machine.sql（SQLite）；依赖 009，必须挂在
-- 009 之后执行。
--
-- 与 SQLite 版的差异：PG 支持直接 DROP CONSTRAINT + ADD CONSTRAINT，
-- 无需重建表。009 的 CHECK 未显式命名（自动生成名），按列集定位删除
-- （沿用 004 按列集定位约束的既定风格）。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. asset_parse_runs.status CHECK 扩全状态机（13 态，含 SUPERSEDED）
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    old_check record;
BEGIN
    FOR old_check IN
        SELECT constraints.conname
        FROM pg_constraint AS constraints
        WHERE constraints.conrelid = 'asset_parse_runs'::regclass
          AND constraints.contype = 'c'
          AND ARRAY(
              SELECT attributes.attname
              FROM unnest(constraints.conkey) AS keys(attnum)
              JOIN pg_attribute AS attributes
                ON attributes.attrelid = constraints.conrelid
               AND attributes.attnum = keys.attnum
              ORDER BY attributes.attname
          ) = ARRAY['status']::name[]
    LOOP
        EXECUTE format(
            'ALTER TABLE asset_parse_runs DROP CONSTRAINT %I',
            old_check.conname
        );
    END LOOP;
END
$$;

ALTER TABLE asset_parse_runs ADD CONSTRAINT ck_asset_parse_runs_status
    CHECK (status IN (
        'QUEUED', 'INSPECTING', 'PLANNED', 'PARSING', 'NORMALIZING',
        'RECONCILING', 'EVALUATING', 'REPAIRING', 'FALLING_BACK',
        'SUCCEEDED', 'FAILED', 'CANCELLED', 'SUPERSEDED'
    ));

-- 成功 Run 关联的快照（M4：转正后回填；SUPERSEDED/FAILED 恒 NULL）。
ALTER TABLE asset_parse_runs ADD COLUMN IF NOT EXISTS snapshot_id TEXT;

-- M4：幂等锚点上移到 Snapshot 指纹（§2.2）。Run 是执行历史，同键多行
-- 合法（FAILED 重跑 / A09 重放 / A07 升级）——009 的唯一索引让位为普通索引。
DROP INDEX IF EXISTS uq_asset_parse_runs_idem;
CREATE INDEX IF NOT EXISTS idx_asset_parse_runs_idem
    ON asset_parse_runs(document_id, source_raw_hash, parser_fingerprint);

-- -----------------------------------------------------------------------------
-- 2. 新表：asset_parse_run_attempts（backend 尝试事件，SRS §4.6/§9.2）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_parse_run_attempts (
    id                  TEXT PRIMARY KEY,
    parse_run_id        TEXT NOT NULL,                  -- M4 补 FK -> asset_parse_runs(id)
    attempt_index       INTEGER NOT NULL CHECK (attempt_index >= 0),
    parser_id           TEXT NOT NULL,
    parser_fingerprint  TEXT NOT NULL,
    attempt_kind        TEXT NOT NULL CHECK (
        attempt_kind IN ('primary', 'fallback', 'repair', 'replay')
    ),
    outcome             TEXT NOT NULL CHECK (outcome IN ('SUCCEEDED', 'FAILED')),
    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ,
    error_message       TEXT,
    metadata_json       JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_parse_run_attempts_seq
    ON asset_parse_run_attempts(parse_run_id, attempt_index);

CREATE INDEX IF NOT EXISTS idx_asset_parse_run_attempts_run
    ON asset_parse_run_attempts(parse_run_id, outcome);

COMMENT ON TABLE  asset_parse_run_attempts IS 'SRS §4.6/§9.2: 每次 backend 尝试（primary/fallback/repair/replay）一行；重试产生新序号，不覆盖旧事件。';
COMMENT ON COLUMN asset_parse_runs.snapshot_id IS 'M4: 成功转正的 Document Snapshot；SUPERSEDED（提交前发现输入过期）与 FAILED 恒为 NULL。';

-- -----------------------------------------------------------------------------
-- 3. asset_document_snapshots.mime_type CHECK 放宽（M4 新链转正需要）
--
-- 001/002 的 legacy 白名单缺 XLSX/PPTX 两个 OOXML MIME；新链对这两种格式
-- 产出的快照必须如实记录真实 MIME（SRS §7.4「不得伪造」精神），扩展白名单。
-- 按列集定位旧 CHECK（002 未显式命名），沿用 004 风格。
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    old_check record;
BEGIN
    FOR old_check IN
        SELECT constraints.conname
        FROM pg_constraint AS constraints
        WHERE constraints.conrelid = 'asset_document_snapshots'::regclass
          AND constraints.contype = 'c'
          AND 'mime_type'::name = ANY(
              SELECT attributes.attname
              FROM unnest(constraints.conkey) AS keys(attnum)
              JOIN pg_attribute AS attributes
                ON attributes.attrelid = constraints.conrelid
               AND attributes.attnum = keys.attnum
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE asset_document_snapshots DROP CONSTRAINT %I',
            old_check.conname
        );
    END LOOP;
END
$$;

ALTER TABLE asset_document_snapshots ADD CONSTRAINT ck_asset_snapshot_mime_type
    CHECK (mime_type IN (
        'text/markdown', 'text/plain', 'text/html', 'application/pdf',
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/octet-stream', 'other'
    ));

-- -----------------------------------------------------------------------------
-- 4. 快照唯一性演进（SRS §8.3A：UNIQUE(domain, snapshot_fingerprint)）
--
-- 004 把 workflow 绑定唯一性建为 partial unique **索引**
-- ``uq_asset_snapshot_workflow_content``（WHERE workflow_graph_hash IS NOT
-- NULL）。新链快照带 snapshot_fingerprint + 哨兵 workflow 绑定：同一内容
-- 在解析管线升级（parser/normalizer/规则变化）后必须能产生**新**快照
-- （A07/A09 语义），旧索引会误拒。重建该索引，额外排除带指纹行——
-- 新链唯一性由 008 的 uq_asset_snapshot_fingerprint 承担。
-- -----------------------------------------------------------------------------
DROP INDEX IF EXISTS uq_asset_snapshot_workflow_content;
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_snapshot_workflow_content
    ON asset_document_snapshots(
        domain, normalized_content_hash, workflow_id, workflow_version,
        workflow_graph_hash
    )
    WHERE workflow_graph_hash IS NOT NULL
      AND snapshot_fingerprint IS NULL;
