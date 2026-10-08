-- 58号：MCP 工具 upload_document → manage_files 改名配套迁移。
-- ① 账本 operation 分类扩容：manage_files(action="replace") 独立记 replace
--    （旧 CHECK 只允许 search/read/upload——不加宽新值会被拒写整条记录）。
-- ② 丢弃老用户的自定义提示词：工具面已换名，旧 instructions 提及的是已不存在的
--    工具名（用户拍板直接丢弃，以官方默认为准）。自定义工具描述无需清理——
--    按工具名查找，旧名挂载的描述查不到自动落回默认。
--
-- codex P2-5（58号二审）：审计表持续写入，ADD CONSTRAINT ... CHECK 的立即全表
-- 校验会持较重锁。新 CHECK 是旧约束的严格超集（search/read/upload ⊂ 追加
-- replace 后的集合）——旧行在旧约束下已全部合法，逐行 VALIDATE 在逻辑上
-- 必然通过，故以 NOT VALID 落约束跳过全表扫描：新写入仍被完整校验，
-- 存量正确性由旧约束的历史强制保证。
ALTER TABLE knowledge_access_records
    DROP CONSTRAINT IF EXISTS ck_knowledge_access_operation;
ALTER TABLE knowledge_access_records
    ADD CONSTRAINT ck_knowledge_access_operation NOT VALID
    CHECK (operation IN ('search', 'read', 'upload', 'replace'));

UPDATE mcp_keys SET instructions = NULL WHERE instructions IS NOT NULL;
