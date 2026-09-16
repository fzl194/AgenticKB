-- 017 · 快照废弃轨道（2026-09-16 删除体系定稿：软删退役、快照 DEPRECATED→7天→回收）。
--
-- lifecycle_status（008 引入）此前只有 READY→DEPRECATED/REVOKED 的服务层原语、
-- 无生产写入方，换范式/内容变更产生的旧快照成为永久僵尸（检索不可见、占据
-- PG 与向量索引）。本迁移补废弃时间戳与索引，供 GC 判定「废弃满 7 天可回收」
-- （7 天缓冲 = 切错范式想切回来不必重花 LLM 钱；指纹命中复活见 snapshot_store）。

ALTER TABLE asset_document_snapshots
    ADD COLUMN IF NOT EXISTS deprecated_at TEXT;

COMMENT ON COLUMN asset_document_snapshots.deprecated_at IS
    '标记 DEPRECATED 的时刻（ISO 时间）；GC 据此判定废弃满 N 天可物理回收，复活时清 NULL';

CREATE INDEX IF NOT EXISTS idx_asset_snapshots_lifecycle
    ON asset_document_snapshots(lifecycle_status, deprecated_at);
