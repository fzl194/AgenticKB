-- =============================================================================
-- 008_object_storage_foundation.sql — Object Storage Foundation (SQLite)
-- =============================================================================
-- SRS §8.5 建议表级边界 / §8.3 现有实体变化 / §8.3A Snapshot 目标字段 / §8.6
-- ADR-0003 D-003（新表进 asset_core）/ D-004（M0 只加表/列，增量幂等，不改读写）
--
-- 本文件为 SQLite 兼容写法（供 sqlite 契约测试与开发期本地库使用）。
-- PostgreSQL 对应版本见 008_object_storage_foundation_postgresql.sql。
-- 两版本表/列/约束必须对齐（D-003）。
--
-- 设计要点：
--   1. 全部 CREATE TABLE IF NOT EXISTS / ADD COLUMN，可在已有数据的库上重复执行。
--   2. 外键（FK）本里程碑不加硬约束（M0 增量、避免与存量数据冲突）；仅 UNIQUE / CHECK
--      立即生效（它们不阻塞存量行）。FK 在注释中标注「M1 补 FK」。
--   3. SQLite 不支持 ADD CONSTRAINT / ALTER COLUMN，新约束只能随 CREATE TABLE 写入；
--      对扩展列上的 CHECK 用 partial UNIQUE INDEX 表达，避免碰存量列类型。
--   4. 时间戳统一 TEXT DEFAULT (datetime('now'))，对齐 001 sqlite 风格。
--   5. nullable object_version_id 的唯一性：SQLite 把多个 NULL 视为「不同」，因此
--      UNIQUE(provider,bucket,object_key,object_version_id) 对「同 key + 两个 NULL」
--      放行——这会让两份 current object 并存。改用表达式索引
--      UNIQUE(provider,bucket,object_key,COALESCE(object_version_id,'')) 统一 NULL 语义
--      （SRS §8.5 末段要求）。
-- =============================================================================


-- -----------------------------------------------------------------------------
-- A. 新表：asset_storage_objects（SRS §8.5 / §3.1A）
--    MinIO 对象定位、hash、size、state、retention。AVAILABLE 前必须完成 SHA-256 校验。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_storage_objects (
    id                TEXT PRIMARY KEY,
    provider          TEXT NOT NULL,                           -- minio | fake | ...
    bucket            TEXT NOT NULL,                           -- 实际 bucket 名（环境前缀已解析）
    object_key        TEXT NOT NULL,                           -- 系统生成、不可变、无业务语义
    object_version_id TEXT,                                    -- 可空：无 versioning 的 bucket 为 NULL
    sha256            TEXT NOT NULL,                           -- 整文件 SHA-256；ETag 不能替代（SRS §8.6）
    size              INTEGER NOT NULL CHECK (size >= 0),      -- 字节；INTEGER 在 SQLite 即 64-bit
    mime              TEXT,
    etag              TEXT,
    artifact_class    TEXT NOT NULL CHECK (
        artifact_class IN ('source','backend_raw','parse_ir','page_render','binary_asset','temporary')
    ),                                                              -- 对齐 contracts.parse_ir.enums.VALID_ARTIFACT_CLASSES
    encryption        TEXT,                                       -- none|sse_s3|sse_kms|cse；M0 仅记录，不强制
    state             TEXT NOT NULL DEFAULT 'STAGING' CHECK (
        state IN ('STAGING','AVAILABLE','QUARANTINED','DELETING','DELETED','MISSING','CORRUPT')
    ),
    retention_until   TEXT,                                       -- 合规保留截止；NULL = 无保留
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    last_verified_at  TEXT                                        -- 最近一次 SHA-256/size 验证时间
);

-- nullable object_version_id 唯一性：NULL 统一视为 ''（SRS §8.5）。
-- SQLite 表达式索引支持 COALESCE；IF NOT EXISTS 保证幂等。
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_storage_objects_location
    ON asset_storage_objects(provider, bucket, object_key, COALESCE(object_version_id, ''));

CREATE INDEX IF NOT EXISTS idx_asset_storage_objects_sha256
    ON asset_storage_objects(sha256);

CREATE INDEX IF NOT EXISTS idx_asset_storage_objects_state
    ON asset_storage_objects(state, created_at);


-- -----------------------------------------------------------------------------
-- B. 新表：asset_upload_sessions（SRS §8.5 / §3.1B / §9.0A）
--    上传状态机：INITIATED -> ... -> COMMITTED / ABORTED / EXPIRED / REJECTED。
--    (kb_id, actor, idempotency_key) 唯一保证客户端重试安全。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_upload_sessions (
    id                 TEXT PRIMARY KEY,
    kb_id              TEXT NOT NULL,                            -- 关联 KB（M1 补 FK -> knowledge_bases(id)）
    folder_id          TEXT,                                    -- 目标文件夹；NULL = 根目录（M1 补 FK -> kb_folders(id)）
    actor              TEXT NOT NULL,                           -- 发起上传的用户 id
    original_filename  TEXT NOT NULL,                           -- 仅展示/审计；不进 object_key
    expected_size      INTEGER CHECK (expected_size IS NULL OR expected_size >= 0),
    expected_mime      TEXT,
    staging_object_key TEXT NOT NULL,                           -- staging bucket 中的临时 key
    idempotency_key    TEXT NOT NULL,                           -- 客户端提供的幂等键
    expires_at         TEXT NOT NULL,                           -- 未提交上传的过期时间
    state              TEXT NOT NULL DEFAULT 'INITIATED' CHECK (
        state IN ('INITIATED','UPLOADING','OBJECT_STAGED','VERIFYING','COMMITTED','ABORTED','EXPIRED','REJECTED')
    ),
    error_message      TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_upload_sessions_idem
    ON asset_upload_sessions(kb_id, actor, idempotency_key);

CREATE INDEX IF NOT EXISTS idx_asset_upload_sessions_state
    ON asset_upload_sessions(state, expires_at);


-- -----------------------------------------------------------------------------
-- C. 新表：asset_storage_object_refs（SRS §8.5）
--    统一引用索引：加速 GC 与审计回溯。owner 表（document/snapshot/asset 等）须各自
--    另持真实 FK 到 asset_storage_objects.id；本表只是冗余索引，不承担完整性（SRS §8.5 末段）。
--    多态 owner_type + owner_id 不加 FK，避免成为唯一完整性保障。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_storage_object_refs (
    storage_object_id TEXT NOT NULL,                             -- M1 补 FK -> asset_storage_objects(id) ON DELETE CASCADE
    owner_type        TEXT NOT NULL,                             -- document | snapshot | parse_asset | binary_asset | ...
    owner_id          TEXT NOT NULL,
    purpose           TEXT NOT NULL,                             -- current_source | parse_ir | page_render | ...
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (storage_object_id, owner_type, owner_id, purpose)
);

CREATE INDEX IF NOT EXISTS idx_asset_storage_object_refs_owner
    ON asset_storage_object_refs(owner_type, owner_id);


-- -----------------------------------------------------------------------------
-- D. 新表：asset_file_audit_events（SRS §8.5）
--    文件管理操作的 append-only 审计流。不是可回滚版本表；before/after 仅记录差异快照。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_file_audit_events (
    id                 TEXT PRIMARY KEY,
    kb_id              TEXT NOT NULL,
    document_id        TEXT,                                     -- M1 补 FK -> asset_documents(id) ON DELETE SET NULL
    storage_object_id  TEXT,                                     -- M1 补 FK -> asset_storage_objects(id) ON DELETE SET NULL
    content_revision   INTEGER,                                  -- 触发本次审计的文档内容版本
    actor              TEXT NOT NULL,
    action             TEXT NOT NULL,                            -- upload | replace | rename | move | delete | restore | ...
    before_json        TEXT NOT NULL DEFAULT '{}',
    after_json         TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_asset_file_audit_events_doc
    ON asset_file_audit_events(kb_id, document_id, created_at);

CREATE INDEX IF NOT EXISTS idx_asset_file_audit_events_object
    ON asset_file_audit_events(storage_object_id);


-- -----------------------------------------------------------------------------
-- E. 新表：asset_storage_quotas（SRS §8.5 命名 kb_storage_quotas；按 D-003 落 asset_core）
--    KB 级存储配额：limit/reserved/used + version 乐观并发。reserved 用于上传会话预留，
--    commit 时转 used，abort 时释放。version 支持并发对账重算。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_storage_quotas (
    kb_id           TEXT PRIMARY KEY,                            -- M1 补 FK -> knowledge_bases(id) ON DELETE CASCADE
    limit_bytes     INTEGER NOT NULL CHECK (limit_bytes >= 0),
    reserved_bytes  INTEGER NOT NULL DEFAULT 0 CHECK (reserved_bytes >= 0),
    used_bytes      INTEGER NOT NULL DEFAULT 0 CHECK (used_bytes >= 0),
    version         INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),  -- 乐观锁
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);


-- -----------------------------------------------------------------------------
-- F. 新表：asset_storage_operations（SRS §8.5 outbox）
--    DB commit 后驱动的存储副作用：promote staging->final / cleanup / GC / 通知。
--    幂等执行（operation_type + payload 决定），可观测重试（attempts / next_retry_at）。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_storage_operations (
    id                  TEXT PRIMARY KEY,
    operation_type      TEXT NOT NULL,                           -- promote | cleanup | gc | notify | verify | ...
    storage_object_id   TEXT,                                    -- M1 补 FK -> asset_storage_objects(id) ON DELETE SET NULL
    upload_session_id   TEXT,                                    -- M1 补 FK -> asset_upload_sessions(id) ON DELETE SET NULL
    payload_json        TEXT NOT NULL DEFAULT '{}',
    state               TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        state IN ('PENDING','IN_PROGRESS','DONE','FAILED','CANCELLED')
    ),
    attempts            INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_retry_at       TEXT,
    last_error          TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_asset_storage_operations_state
    ON asset_storage_operations(state, next_retry_at);


-- =============================================================================
-- B. 扩展现有表（ADD COLUMN IF NOT EXISTS；先核对现有列，勿重复加）
-- 现有 asset_documents 已含：kb_id, storage_path, directory_path, owner_id,
-- file_size, modified_at（来自 001 + 004_kb_isolation + 005_kb_file_meta）。
-- storage_path 保留为 legacy（SRS §8.3 / §8.7）。
-- =============================================================================

-- asset_documents（SRS §8.3）：当前内容指针、源 hash、内容版本与软删/恢复
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS folder_id             TEXT;        -- M1 补 FK -> kb_folders(id) ON DELETE RESTRICT
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS storage_object_id     TEXT;        -- M1 补 FK -> asset_storage_objects(id)；指向当前内容对象
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS source_raw_hash       TEXT;        -- 当前内容的原始 SHA-256（与 storage_object 校验一致）
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS content_revision      INTEGER NOT NULL DEFAULT 0 CHECK (content_revision >= 0);  -- 乐观并发版本
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS content_updated_at    TEXT;        -- 当前内容最后替换时间
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS deleted_at            TEXT;        -- 软删时间戳；NULL = 未删
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS restored_at           TEXT;        -- 最近一次从软删恢复时间

CREATE INDEX IF NOT EXISTS idx_asset_documents_storage_object
    ON asset_documents(storage_object_id) WHERE storage_object_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_asset_documents_deleted_at
    ON asset_documents(kb_id, deleted_at) WHERE deleted_at IS NOT NULL;


-- asset_document_snapshots（SRS §8.3A）：解析/编译指纹、Parse IR 对象、质量与生命周期
-- 现有列保留：id/domain/normalized_content_hash/raw_content_hash/mime_type/title/
-- parser_profile_json/workflow_*/metadata_json/created_at（见 001 + 004 snapshot binding）。
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS snapshot_fingerprint         TEXT;      -- 唯一身份指纹（raw+parser+workflow+IR+compiler）
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS parse_ir_storage_object_id   TEXT;      -- M1 补 FK -> asset_storage_objects(id)
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS parse_ir_schema_version      TEXT;      -- 解释 IR 的契约版本
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS parser_fingerprint           TEXT;      -- parser 代码/模型/配置/依赖合成指纹
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS compiler_fingerprint         TEXT;      -- 切片/视图编译策略指纹
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS quality_status               TEXT CHECK (
    quality_status IS NULL OR quality_status IN ('PASS','WARN','FAIL')
);
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS lifecycle_status             TEXT NOT NULL DEFAULT 'READY' CHECK (
    lifecycle_status IN ('READY','DEPRECATED','REVOKED')
);
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS created_by_run_id            TEXT;      -- 产生该 Snapshot 的 Mining Run

-- snapshot_fingerprint 唯一性：仅在指纹非 NULL 时生效（M0 存量快照无指纹，不阻塞）。
-- 与 §8.3A「UNIQUE(domain, snapshot_fingerprint)」对齐；partial 避开 NULL 行。
-- SQLite partial index + IF NOT EXISTS 幂等。重复指纹数据存在时由应用层 upsert 去重，
-- 待清理后再补硬约束（参见 004 snapshot binding 的处理方式）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_snapshot_fingerprint
    ON asset_document_snapshots(domain, snapshot_fingerprint)
    WHERE snapshot_fingerprint IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_asset_snapshot_parse_ir_object
    ON asset_document_snapshots(parse_ir_storage_object_id) WHERE parse_ir_storage_object_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_asset_snapshot_lifecycle
    ON asset_document_snapshots(domain, lifecycle_status);


-- asset_document_snapshot_links（SRS §8.3 / §8.3A 末段）：来源对象与内容版本
-- source_uri 保留为 legacy（SRS §8.3）。
ALTER TABLE asset_document_snapshot_links ADD COLUMN IF NOT EXISTS source_storage_object_id  TEXT;     -- M1 补 FK -> asset_storage_objects(id)
ALTER TABLE asset_document_snapshot_links ADD COLUMN IF NOT EXISTS source_content_revision    INTEGER; -- 链接建立时文档的内容版本

CREATE INDEX IF NOT EXISTS idx_asset_snapshot_links_source_object
    ON asset_document_snapshot_links(source_storage_object_id) WHERE source_storage_object_id IS NOT NULL;
