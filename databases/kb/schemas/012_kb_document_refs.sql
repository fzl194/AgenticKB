-- 012（OneNet，47 号 §四-4/§四-5）：业务 KB → 一张网公共库文档的引用关系。
--
-- 引用即只读授权：引用文档通过本库进入检索/结构/证据范围（范围 UNION 详见
-- 47 号三类口径），不可移动/改名/删除/重挖（只能取消引用）。挖掘枚举
-- （_kb_object_documents）保持 owned-only，不读本表。
--
-- 同域约束（document 必须属同域公共库）由 refs_service 服务端强制校验
-- （跨域引用在域库拆分时会断裂，不建 FK 兜底——公共库文档软删后引用行由
-- 导入任务同步清理）。

SELECT pg_advisory_xact_lock(
    hashtextextended('agentickb:kb-document-refs-v1', 0)
);

CREATE TABLE IF NOT EXISTS kb_document_refs (
    kb_id       TEXT NOT NULL,
    document_id TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (kb_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_kb_document_refs_document
    ON kb_document_refs (document_id);
