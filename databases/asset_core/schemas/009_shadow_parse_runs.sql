-- =============================================================================
-- 009_shadow_parse_runs.sql — Shadow Parse 运行投影（SQLite）
-- =============================================================================
-- 文档解析平台化 M2（SRS §2.2 幂等复用 / §C08 Shadow Parse / §4.6）
-- ADR-0003 D-003（新表进 asset_core）/ D-004（M0/M2 只加表，增量幂等，不改读写）
--
-- 本文件为 SQLite 兼容写法（供 sqlite 契约测试与开发期本地库使用）。
-- PostgreSQL 对应版本见 009_shadow_parse_runs_postgresql.sql。
-- 两版本表/列/约束必须对齐（D-003）。
--
-- 设计要点：
--   1. 影子解析与现有发布链路硬隔离：本表只是「一次解析执行」的运行摘要投影，
--      绝不写 asset_document_snapshots / asset_raw_segments / mining_run_documents
--      （M2 退出条件：不影响现有发布）。
--   2. 影子运行无状态机（M4 才引入完整 Parse Run 状态机）：一次执行直接落终态
--      SUCCEEDED / FAILED，无中间态、无可恢复暂停点。
--   3. 幂等键 UNIQUE(document_id, source_raw_hash, parser_fingerprint)：同输入
--      同 parser 复用已有制品与投影行（SRS §2.2 幂等复用），由应用层 upsert
--      ON CONFLICT 覆盖（FAILED 重跑可翻转为 SUCCEEDED）。
--   4. 全部 CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS，可重跑。
--   5. 依赖 008（parse_ir_storage_object_id 指向 asset_storage_objects 注册行）。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 新表：asset_parse_runs（影子解析运行投影，SRS §2.2 / §C08）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_parse_runs (
    id                          TEXT PRIMARY KEY,               -- parse run 业务 id（Repository 分配）
    document_id                 TEXT NOT NULL,                  -- M4 补 FK -> asset_documents(id)
    source_storage_object_id    TEXT NOT NULL,                  -- 本次运行冻结的输入对象；M4 补 FK -> asset_storage_objects(id)
    source_raw_hash             TEXT NOT NULL,                  -- 输入内容 SHA-256（幂等键成员）
    source_content_revision     INTEGER NOT NULL,               -- 冻结时的文档内容版本
    parser_id                   TEXT NOT NULL,                  -- 后端注册 id（SRS §C04）
    parser_fingerprint          TEXT NOT NULL,                  -- parser 代码/模型/配置/依赖合成指纹（幂等键成员，SRS §3.5）
    parse_ir_storage_object_id  TEXT,                           -- 成功后指向 parse bucket 的 IR 制品；NULL = 未产出
    parse_ir_schema_version     TEXT,                           -- 解释该 IR 的契约版本
    element_count               INTEGER,                        -- 投影摘要：element 数
    container_count             INTEGER,                        -- 投影摘要：container 数
    relation_count              INTEGER,                        -- 投影摘要：relation 数
    status                      TEXT NOT NULL CHECK (status IN ('SUCCEEDED','FAILED')),  -- 影子运行直接落终态
    error_message               TEXT,                           -- FAILED 时的错误摘要
    started_at                  TEXT NOT NULL,
    finished_at                 TEXT,
    metadata_json               TEXT NOT NULL DEFAULT '{}'      -- parser 版本 / warnings / mode=shadow 等
);

-- 幂等探针索引（M4 修订：普通索引）。Run 是执行历史，同键多行合法
-- （FAILED 重跑 / A09 重放 / A07 升级）——幂等锚点在 Snapshot 指纹
-- （SRS §2.2/§8.3A），010 负责把既有环境的唯一索引降级为本索引。
CREATE INDEX IF NOT EXISTS idx_asset_parse_runs_idem
    ON asset_parse_runs(document_id, source_raw_hash, parser_fingerprint);

-- 常用探查：按文档看历史运行 / 按 parser 指纹做回归比对。
CREATE INDEX IF NOT EXISTS idx_asset_parse_runs_document
    ON asset_parse_runs(document_id, status);

CREATE INDEX IF NOT EXISTS idx_asset_parse_runs_parser
    ON asset_parse_runs(parser_fingerprint, status);
