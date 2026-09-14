-- 016（OneNet，47 号 §四-4）：知识一张网切片接入登记面。
--
--   * onenet_imports  —— 导入记录=订阅（勾选范围/游标/版本/状态机）；
--     UNIQUE(domain, source_id)：同源重复导入拒绝，更新走重同步。
--   * onenet_toc_cache —— 管理员向导的章节树预览缓存（发生在导入记录
--     创建之前，独立落缓存；parsed_version 匹配才复用）。
--
-- 产物文档/目录落在域公共库的既有表（asset_documents/kb_folders），本迁移
-- 不动身份模型；kb_document_refs 见 databases/kb/012。

SELECT pg_advisory_xact_lock(
    hashtextextended('agentickb:onenet-imports-v1', 0)
);

CREATE TABLE IF NOT EXISTS onenet_imports (
    id                     TEXT PRIMARY KEY,
    domain                 TEXT NOT NULL,
    source_id              TEXT NOT NULL,
    doc_name               TEXT,
    parsed_version_seen    TEXT,
    total_slices           BIGINT,
    fetched_max_part_id    BIGINT,
    -- 勾选范围 + 规则版本（Selection.to_dict() + rule/mapping 版本，47 号 §七）
    selection_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                 TEXT NOT NULL
                           CHECK (status IN (
                               'queued', 'fetching', 'restoring',
                               'importing', 'mining', 'done', 'failed')),
    kb_id                  TEXT NOT NULL,
    folder_root_path       TEXT,
    document_count         INTEGER,
    error                  TEXT,
    created_by             TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    UNIQUE (domain, source_id)
);

CREATE INDEX IF NOT EXISTS idx_onenet_imports_domain_status
    ON onenet_imports (domain, status);

CREATE TABLE IF NOT EXISTS onenet_toc_cache (
    domain                 TEXT NOT NULL,
    source_id              TEXT NOT NULL,
    parsed_version_seen    TEXT,
    toc_json               JSONB NOT NULL,
    scanned_at             TEXT NOT NULL,
    PRIMARY KEY (domain, source_id)
);
