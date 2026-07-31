-- KB 文件夹（一等文件夹实体）。kb_folders 是文件夹结构的唯一真相源；磁盘目录与之镜像。
-- asset_documents.directory_path 仍保留（挖掘 walk 与 storage_path 派生依赖它），其值
-- = 文件所在文件夹的 path（根为 ""）。允许空文件夹、文件夹元数据、整子树移动。

CREATE TABLE IF NOT EXISTS kb_folders (
    id          TEXT PRIMARY KEY,
    kb_id       TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    parent_id   TEXT REFERENCES kb_folders(id) ON DELETE CASCADE,   -- NULL = 顶层
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,                 -- 规范化完整路径，如 "5G/AMF"；顶层 = name
    created_at  TEXT NOT NULL,
    created_by  TEXT REFERENCES kb_users(id) ON DELETE SET NULL,
    CONSTRAINT uq_kb_folders_kb_path UNIQUE (kb_id, path)           -- path 唯一，兜底防重（含顶层）
);

-- 同一父下同名（parent_id 非 NULL 时由 (kb_id,parent_id,name) 兜底；NULL 时 path 唯一兜底）
CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_folders_kb_parent_name
    ON kb_folders(kb_id, parent_id, name) WHERE parent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_kb_folders_kb_parent ON kb_folders(kb_id, parent_id);
