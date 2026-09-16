-- 013 · KB 硬删任务化（2026-09-16：删除体系二期——禁用态 + 后台删除 + 进度）.
--
-- 内网实测：数万文档的库同步硬删耗时数十分钟，请求被网关 300s 掐断、
-- 库行最后才删导致"刷新还在"，用户全程无感知。二期语义：
--   DELETE /api/kb/{id} 确认后秒级置 status='deleting'（禁用：读写全退、
--   检索自动退出——Python is_visible 与 Java KnowledgeBaseMapper 均已滤
--   status='active'），后台任务执行管线，前端轮询本表渲染进度。
-- 重启恢复：startup 扫 running/queued 任务重新入队（管线幂等）。

ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS knowledge_bases_status_check;
ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS ck_knowledge_bases_status;
ALTER TABLE knowledge_bases
    ADD CONSTRAINT ck_knowledge_bases_status
    CHECK (status IN ('active', 'deleting', 'deleted'));

CREATE TABLE IF NOT EXISTS kb_purge_tasks (
    id            TEXT PRIMARY KEY,
    kb_id         TEXT NOT NULL,
    kb_name       TEXT NOT NULL,
    domain        TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    phase         TEXT NOT NULL DEFAULT 'queued',
    progress_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    requested_by  TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kb_purge_tasks_domain
    ON kb_purge_tasks(domain, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_purge_tasks_kb
    ON kb_purge_tasks(kb_id);
