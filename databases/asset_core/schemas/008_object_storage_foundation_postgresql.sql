-- =============================================================================
-- 008_object_storage_foundation_postgresql.sql — Object Storage Foundation (PG)
-- =============================================================================
-- SRS §8.5 建议表级边界 / §8.3 现有实体变化 / §8.3A Snapshot 目标字段 / §8.6
-- ADR-0003 D-003（新表进 asset_core）/ D-004（M0 只加表/列，增量幂等，不改读写）
--
-- PostgreSQL 版本，对齐 008_object_storage_foundation.sql（SQLite）。
-- 两版本表/列/约束必须一致（D-003）。
--
-- 设计要点：
--   1. 全部 CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS，幂等可重跑。
--   2. FK 本里程碑不加硬约束（M0 增量，避免与存量数据冲突）；仅 UNIQUE / CHECK 立即生效。
--      ADD CONSTRAINT 不支持 IF NOT EXISTS，用 DO 块 + pg_constraint 守卫幂等（沿用 004 风格）。
--   3. 时间戳 TIMESTAMPTZ DEFAULT now()，对齐 002 postgres 风格。
--   4. nullable object_version_id 唯一性：PostgreSQL 多 NULL 不冲突，故
--      UNIQUE(provider,bucket,object_key,object_version_id) 会放过两个 NULL（两份 current
--      object 并存）。改用表达式唯一索引 COALESCE(object_version_id,'')（SRS §8.5 末段）。
-- =============================================================================


-- -----------------------------------------------------------------------------
-- A. 新表：asset_storage_objects（SRS §8.5 / §3.1A）
--    MinIO 对象定位、hash、size、state、retention。AVAILABLE 前必须完成 SHA-256 校验。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_storage_objects (
    id                TEXT PRIMARY KEY,
    provider          TEXT NOT NULL,
    bucket            TEXT NOT NULL,
    object_key        TEXT NOT NULL,
    object_version_id TEXT,                                    -- 可空：无 versioning 的 bucket 为 NULL
    sha256            TEXT NOT NULL,                           -- 整文件 SHA-256；ETag 不能替代（SRS §8.6）
    size              BIGINT NOT NULL CHECK (size >= 0),       -- 字节
    mime              TEXT,
    etag              TEXT,
    artifact_class    TEXT NOT NULL CHECK (
        artifact_class IN ('source','backend_raw','parse_ir','page_render','binary_asset','temporary')
    ),                                                              -- 对齐 contracts.parse_ir.enums.VALID_ARTIFACT_CLASSES
    encryption        TEXT,                                       -- none|sse_s3|sse_kms|cse；M0 仅记录，不强制
    state             TEXT NOT NULL DEFAULT 'STAGING' CHECK (
        state IN ('STAGING','AVAILABLE','QUARANTINED','DELETING','DELETED','MISSING','CORRUPT')
    ),
    retention_until   TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_verified_at  TIMESTAMPTZ
);

-- nullable object_version_id 唯一性：NULL 统一视为 ''（SRS §8.5 末段）。
-- 表达式唯一索引在 PG 上处理 NULL 语义比列级 UNIQUE 更安全。
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_storage_objects_location
    ON asset_storage_objects(provider, bucket, object_key, COALESCE(object_version_id, ''));

CREATE INDEX IF NOT EXISTS idx_asset_storage_objects_sha256
    ON asset_storage_objects(sha256);

CREATE INDEX IF NOT EXISTS idx_asset_storage_objects_state
    ON asset_storage_objects(state, created_at);

COMMENT ON TABLE  asset_storage_objects IS 'SRS §8.5/§3.1A: MinIO 对象定位、hash、size、state、retention；AVAILABLE 前必须 SHA-256 校验。';
COMMENT ON COLUMN asset_storage_objects.object_version_id IS '可空；NULL 经 COALESCE 唯一索引归一，避免两份 current object 并存（SRS §8.5 末段）。';


-- =============================================================================
-- B. 扩展现有表（ADD COLUMN IF NOT EXISTS；先核对现有列）
-- 现有 asset_documents 已含：kb_id, storage_path, directory_path, owner_id,
-- file_size, modified_at（001 + 004_kb_isolation + 005_kb_file_meta）。
-- storage_path 保留为 legacy（SRS §8.3 / §8.7）。
-- =============================================================================

-- asset_documents（SRS §8.3）：当前内容指针、源 hash、内容版本与软删/恢复
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS folder_id             TEXT;
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS storage_object_id     TEXT;        -- M1 补 FK -> asset_storage_objects(id)
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS source_raw_hash       TEXT;
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS content_revision      INTEGER NOT NULL DEFAULT 0 CHECK (content_revision >= 0);
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS content_updated_at    TIMESTAMPTZ;
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS deleted_at            TIMESTAMPTZ;
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS restored_at           TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_asset_documents_storage_object
    ON asset_documents(storage_object_id) WHERE storage_object_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_asset_documents_deleted_at
    ON asset_documents(kb_id, deleted_at) WHERE deleted_at IS NOT NULL;

COMMENT ON COLUMN asset_documents.storage_object_id  IS 'SRS §8.3: 当前内容对象；M1 加 FK -> asset_storage_objects.id。';
COMMENT ON COLUMN asset_documents.source_raw_hash    IS 'SRS §8.3: 当前内容原始 SHA-256，与 storage_object 校验一致。';
COMMENT ON COLUMN asset_documents.content_revision   IS 'SRS §8.3: 内容版本，乐观并发。';
COMMENT ON COLUMN asset_documents.deleted_at         IS 'SRS §8.3: 软删时间戳；NULL = 未删。';
COMMENT ON COLUMN asset_documents.restored_at        IS 'SRS §8.3: 最近一次从软删恢复时间。';
COMMENT ON COLUMN asset_documents.storage_path       IS 'legacy 物理路径；M0 保留只读，M1 起仅作回退（SRS §8.7）。';


-- asset_document_snapshots（SRS §8.3A）：解析/编译指纹、Parse IR 对象、质量与生命周期
-- 现有列保留：id/domain/normalized_content_hash/raw_content_hash/mime_type/title/
-- parser_profile_json/workflow_*/metadata_json/created_at（001 + 004 snapshot binding）。
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS snapshot_fingerprint         TEXT;
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS parse_ir_storage_object_id   TEXT;      -- M1 补 FK -> asset_storage_objects(id)
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS parse_ir_schema_version      TEXT;
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS parser_fingerprint           TEXT;
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS compiler_fingerprint         TEXT;
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS quality_status               TEXT CHECK (
    quality_status IS NULL OR quality_status IN ('PASS','WARN','FAIL')
);
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS lifecycle_status             TEXT NOT NULL DEFAULT 'READY' CHECK (
    lifecycle_status IN ('READY','DEPRECATED','REVOKED')
);
ALTER TABLE asset_document_snapshots ADD COLUMN IF NOT EXISTS created_by_run_id            TEXT;

-- snapshot_fingerprint 唯一性：仅在指纹非 NULL 时生效（M0 存量快照无指纹，不阻塞）。
-- 与 §8.3A「UNIQUE(domain, snapshot_fingerprint)」对齐；partial 避开 NULL 行。
-- 重复指纹数据存在时跳过建索引（参见 004 snapshot binding 的处理方式），待清理后再强约束。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM asset_document_snapshots
        WHERE snapshot_fingerprint IS NOT NULL
        GROUP BY domain, snapshot_fingerprint HAVING COUNT(*) > 1
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_snapshot_fingerprint
            ON asset_document_snapshots(domain, snapshot_fingerprint)
            WHERE snapshot_fingerprint IS NOT NULL;
    ELSE
        RAISE NOTICE 'Skip uq_asset_snapshot_fingerprint: duplicate (domain, snapshot_fingerprint) rows exist.';
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_asset_snapshot_parse_ir_object
    ON asset_document_snapshots(parse_ir_storage_object_id) WHERE parse_ir_storage_object_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_asset_snapshot_lifecycle
    ON asset_document_snapshots(domain, lifecycle_status);

COMMENT ON COLUMN asset_document_snapshots.snapshot_fingerprint       IS 'SRS §8.3A: Snapshot 唯一身份指纹（raw+parser+workflow+IR+compiler）。';
COMMENT ON COLUMN asset_document_snapshots.parse_ir_storage_object_id IS 'SRS §8.3A: 指向 MinIO 中完整 canonical Parse IR；M1 加 FK。';
COMMENT ON COLUMN asset_document_snapshots.quality_status             IS 'SRS §8.3A: PASS/WARN；FAIL 不创建 READY Snapshot。';
COMMENT ON COLUMN asset_document_snapshots.lifecycle_status           IS 'SRS §8.3A: READY/DEPRECATED/REVOKED。';


-- asset_document_snapshot_links（SRS §8.3 / §8.3A 末段）：来源对象与内容版本
-- source_uri 保留为 legacy（SRS §8.3）。
ALTER TABLE asset_document_snapshot_links ADD COLUMN IF NOT EXISTS source_storage_object_id  TEXT;     -- M1 补 FK -> asset_storage_objects(id)
ALTER TABLE asset_document_snapshot_links ADD COLUMN IF NOT EXISTS source_content_revision    INTEGER;

CREATE INDEX IF NOT EXISTS idx_asset_snapshot_links_source_object
    ON asset_document_snapshot_links(source_storage_object_id) WHERE source_storage_object_id IS NOT NULL;

COMMENT ON COLUMN asset_document_snapshot_links.source_storage_object_id IS 'SRS §8.3: 该链接固定的来源对象；source_uri 降级为 legacy。';
