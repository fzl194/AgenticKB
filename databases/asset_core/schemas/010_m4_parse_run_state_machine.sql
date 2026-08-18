-- =============================================================================
-- 010_m4_parse_run_state_machine.sql — M4 Parse Run 状态机扩列（SQLite）
-- =============================================================================
-- 文档解析平台化 M4（SRS §9.2 完整 Parse Run 状态机 + §9.5 SUPERSEDED +
-- §4.6 attempt 事件）。ADR-0003 D-003 / D-004（增量幂等，不改读写）。
--
-- 内容：
--   1. asset_parse_runs.status CHECK 从两态（SUCCEEDED/FAILED）扩到完整
--      状态机 13 态，与 contracts/state_machines.py 的
--      VALID_PARSE_RUN_STATES 单一事实源对齐（含 SUPERSEDED 终态）。
--   2. 新表 asset_parse_run_attempts：每个 backend 尝试一行（SRS §2.2
--      「fallback 必须留下原因」/ §9.2「重试创建新的 attempt event」）。
--
-- SQLite 不支持修改列级 CHECK，采用标准重建表模式（新建→搬运→删旧→改名
-- →重建索引）。幂等性：新表名带 _m4 后缀，重跑时旧表已不存在、SELECT
-- 搬运为空操作；attempts 表 CREATE IF NOT EXISTS。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. asset_parse_runs 重建（status CHECK 扩全状态机）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_parse_runs_m4 (
    id                          TEXT PRIMARY KEY,
    document_id                 TEXT NOT NULL,
    source_storage_object_id    TEXT NOT NULL,
    source_raw_hash             TEXT NOT NULL,
    source_content_revision     INTEGER NOT NULL,
    parser_id                   TEXT NOT NULL,
    parser_fingerprint          TEXT NOT NULL,
    parse_ir_storage_object_id  TEXT,
    parse_ir_schema_version     TEXT,
    element_count               INTEGER,
    container_count             INTEGER,
    relation_count              INTEGER,
    snapshot_id                 TEXT,
    status                      TEXT NOT NULL CHECK (status IN (
        'QUEUED', 'INSPECTING', 'PLANNED', 'PARSING', 'NORMALIZING',
        'RECONCILING', 'EVALUATING', 'REPAIRING', 'FALLING_BACK',
        'SUCCEEDED', 'FAILED', 'CANCELLED', 'SUPERSEDED'
    )),
    error_message               TEXT,
    started_at                  TEXT NOT NULL,
    finished_at                 TEXT,
    metadata_json               TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO asset_parse_runs_m4 (
    id, document_id, source_storage_object_id, source_raw_hash,
    source_content_revision, parser_id, parser_fingerprint,
    parse_ir_storage_object_id, parse_ir_schema_version, element_count,
    container_count, relation_count, status, error_message, started_at,
    finished_at, metadata_json
)
SELECT
    id, document_id, source_storage_object_id, source_raw_hash,
    source_content_revision, parser_id, parser_fingerprint,
    parse_ir_storage_object_id, parse_ir_schema_version, element_count,
    container_count, relation_count, status, error_message, started_at,
    finished_at, metadata_json
FROM asset_parse_runs;

DROP TABLE asset_parse_runs;
ALTER TABLE asset_parse_runs_m4 RENAME TO asset_parse_runs;

-- M4：幂等锚点上移到 Snapshot 指纹（§2.2）。Run 是执行历史，同键多行
-- 合法（FAILED 重跑 / A09 重放 / A07 升级）——唯一索引让位为普通索引。
DROP INDEX IF EXISTS uq_asset_parse_runs_idem;
CREATE INDEX IF NOT EXISTS idx_asset_parse_runs_idem
    ON asset_parse_runs(document_id, source_raw_hash, parser_fingerprint);

CREATE INDEX IF NOT EXISTS idx_asset_parse_runs_document
    ON asset_parse_runs(document_id, status);

CREATE INDEX IF NOT EXISTS idx_asset_parse_runs_parser
    ON asset_parse_runs(parser_fingerprint, status);

-- -----------------------------------------------------------------------------
-- 2. 新表：asset_parse_run_attempts（backend 尝试事件，SRS §4.6/§9.2）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_parse_run_attempts (
    id                  TEXT PRIMARY KEY,
    parse_run_id        TEXT NOT NULL,             -- M4 补 FK -> asset_parse_runs(id)
    attempt_index       INTEGER NOT NULL CHECK (attempt_index >= 0),
    parser_id           TEXT NOT NULL,
    parser_fingerprint  TEXT NOT NULL,
    attempt_kind        TEXT NOT NULL CHECK (
        attempt_kind IN ('primary', 'fallback', 'repair', 'replay')
    ),
    outcome             TEXT NOT NULL CHECK (outcome IN ('SUCCEEDED', 'FAILED')),
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    error_message       TEXT,
    metadata_json       TEXT NOT NULL DEFAULT '{}'
);

-- 幂等：同一 run 的尝试序号唯一（重试必产生新序号，不覆盖旧事件）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_parse_run_attempts_seq
    ON asset_parse_run_attempts(parse_run_id, attempt_index);

CREATE INDEX IF NOT EXISTS idx_asset_parse_run_attempts_run
    ON asset_parse_run_attempts(parse_run_id, outcome);

-- -----------------------------------------------------------------------------
-- 3. asset_document_snapshots 重建（mime_type CHECK 放宽，对齐 010 PG 第 3 节）
--
-- SQLite baseline 链 = 001 + 008 + 009 + 010（004 workflow 绑定为 PG-only，
-- SQLite 快照表无 workflow 列）。此处重建保留 001 + 008 全部列，仅扩展
-- mime 白名单（补 XLSX/PPTX OOXML MIME，新链如实记录真实 MIME）。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_document_snapshots_m4 (
    id                      TEXT PRIMARY KEY,
    domain                  TEXT NOT NULL,
    normalized_content_hash TEXT NOT NULL,
    raw_content_hash        TEXT NOT NULL,
    mime_type               TEXT NOT NULL CHECK (
        mime_type IN (
            'text/markdown', 'text/plain', 'text/html', 'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'application/octet-stream', 'other'
        )
    ),
    title                   TEXT,
    scope_json              TEXT NOT NULL DEFAULT '{}',
    tags_json               TEXT NOT NULL DEFAULT '[]',
    parser_profile_json     TEXT NOT NULL DEFAULT '{}',
    metadata_json           TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    snapshot_fingerprint         TEXT,
    parse_ir_storage_object_id   TEXT,
    parse_ir_schema_version      TEXT,
    parser_fingerprint           TEXT,
    compiler_fingerprint         TEXT,
    quality_status               TEXT CHECK (
        quality_status IS NULL OR quality_status IN ('PASS','WARN','FAIL')
    ),
    lifecycle_status             TEXT NOT NULL DEFAULT 'READY' CHECK (
        lifecycle_status IN ('READY','DEPRECATED','REVOKED')
    ),
    created_by_run_id            TEXT
);

INSERT INTO asset_document_snapshots_m4 (
    id, domain, normalized_content_hash, raw_content_hash, mime_type, title,
    scope_json, tags_json, parser_profile_json, metadata_json, created_at,
    snapshot_fingerprint, parse_ir_storage_object_id, parse_ir_schema_version,
    parser_fingerprint, compiler_fingerprint, quality_status,
    lifecycle_status, created_by_run_id
)
SELECT
    id, domain, normalized_content_hash, raw_content_hash, mime_type, title,
    scope_json, tags_json, parser_profile_json, metadata_json, created_at,
    snapshot_fingerprint, parse_ir_storage_object_id, parse_ir_schema_version,
    parser_fingerprint, compiler_fingerprint, quality_status,
    lifecycle_status, created_by_run_id
FROM asset_document_snapshots;

DROP TABLE asset_document_snapshots;
ALTER TABLE asset_document_snapshots_m4 RENAME TO asset_document_snapshots;

-- 008 在原表上建的索引随 DROP 消失，按 008 原样重建。
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_snapshot_fingerprint
    ON asset_document_snapshots(domain, snapshot_fingerprint)
    WHERE snapshot_fingerprint IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_asset_snapshot_parse_ir_object
    ON asset_document_snapshots(parse_ir_storage_object_id)
    WHERE parse_ir_storage_object_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_asset_snapshot_lifecycle
    ON asset_document_snapshots(domain, lifecycle_status);
