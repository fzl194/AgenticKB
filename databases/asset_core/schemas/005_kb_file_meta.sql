-- KB 文件管理：asset_documents 增文件大小与修改时间（文件管理器列表展示）。
-- file_size：字节大小；modified_at：源文件 mtime（上传时 stat 捕获）。
-- 幂等（IF NOT EXISTS），向后兼容存量行（NULL）。

ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS file_size BIGINT;
ALTER TABLE asset_documents ADD COLUMN IF NOT EXISTS modified_at TEXT;
