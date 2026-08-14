-- =============================================================================
-- 009_shadow_parse_runs_postgresql.sql — Shadow Parse 运行投影（PG）
-- =============================================================================
-- 文档解析平台化 M2（SRS §2.2 幂等复用 / §C08 Shadow Parse / §4.6）
-- ADR-0003 D-003 / D-004（增量幂等，不改读写）
--
-- PostgreSQL 版本，对齐 009_shadow_parse_runs.sql（SQLite）。两版本表/列/约束
-- 必须一致（D-003）。依赖 008（parse_ir_storage_object_id 指向
-- asset_storage_objects 注册行），必须挂在 008 之后执行。
--
-- 设计要点（同 SQLite 版）：
--   1. 影子解析与现有发布链路硬隔离：只投影运行摘要，不写
--      asset_document_snapshots / asset_raw_segments / mining_run_documents。
--   2. 影子运行无状态机（M4 才有完整 Parse Run 状态机）：直接落终态。
--   3. 幂等键 UNIQUE(document_id, source_raw_hash, parser_fingerprint)，
--      应用层 ON CONFLICT DO UPDATE 覆盖（FAILED 重跑可翻转为 SUCCEEDED）。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 新表：asset_parse_runs（影子解析运行投影，SRS §2.2 / §C08）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_parse_runs (
    id                          TEXT PRIMARY KEY,
    document_id                 TEXT NOT NULL,                  -- M4 补 FK -> asset_documents(id)
    source_storage_object_id    TEXT NOT NULL,                  -- M4 补 FK -> asset_storage_objects(id)
    source_raw_hash             TEXT NOT NULL,
    source_content_revision     INTEGER NOT NULL,
    parser_id                   TEXT NOT NULL,
    parser_fingerprint          TEXT NOT NULL,
    parse_ir_storage_object_id  TEXT,                           -- M4 补 FK -> asset_storage_objects(id)
    parse_ir_schema_version     TEXT,
    element_count               INTEGER,
    container_count             INTEGER,
    relation_count              INTEGER,
    status                      TEXT NOT NULL CHECK (status IN ('SUCCEEDED','FAILED')),
    error_message               TEXT,
    started_at                  TIMESTAMPTZ NOT NULL,
    finished_at                 TIMESTAMPTZ,
    metadata_json               JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- 幂等键：同 document + 同内容 + 同 parser 只有一行投影（SRS §2.2）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_parse_runs_idem
    ON asset_parse_runs(document_id, source_raw_hash, parser_fingerprint);

CREATE INDEX IF NOT EXISTS idx_asset_parse_runs_document
    ON asset_parse_runs(document_id, status);

CREATE INDEX IF NOT EXISTS idx_asset_parse_runs_parser
    ON asset_parse_runs(parser_fingerprint, status);

COMMENT ON TABLE  asset_parse_runs IS 'SRS §2.2/§C08: M2 影子解析运行投影；一次执行直接落 SUCCEEDED/FAILED 终态，不进发布链路。';
COMMENT ON COLUMN asset_parse_runs.source_raw_hash IS '输入内容 SHA-256；幂等键成员（document_id, source_raw_hash, parser_fingerprint）。';
COMMENT ON COLUMN asset_parse_runs.parser_fingerprint IS 'parser 代码/模型/配置/依赖合成指纹（SRS §3.5）；幂等键成员。';
COMMENT ON COLUMN asset_parse_runs.parse_ir_storage_object_id IS '成功后指向 parse bucket 的 IR 制品注册行；NULL = 未产出。';
COMMENT ON COLUMN asset_parse_runs.status IS '影子运行无状态机（M4 才引入完整 Parse Run 状态机）：一次执行直接落终态。';
