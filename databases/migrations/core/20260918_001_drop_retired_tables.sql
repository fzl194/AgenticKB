-- 52号数据库收敛：仅在兼容代码已部署、目标库克隆与业务等价验证完成后执行。
-- 禁止 CASCADE；任何未知依赖都必须让事务失败并人工复核。

-- 51号批次3兼容尾巴：旧 MCP 表可能仍物理存在。删除前逐钥匙和
-- “迁移后钥匙同域内仍 active 的开放库”核对，禁止用总行数冒充完整性。
DO $$
BEGIN
    IF (to_regclass('public.mcp_access') IS NULL)
       <> (to_regclass('public.mcp_open_kbs') IS NULL) THEN
        RAISE EXCEPTION 'legacy MCP tables are in an inconsistent half-present state';
    END IF;

    IF to_regclass('public.mcp_access') IS NOT NULL THEN
        IF EXISTS (
            SELECT 1
              FROM mcp_access old_key
              LEFT JOIN mcp_keys new_key ON new_key.user_id = old_key.user_id
             WHERE new_key.id IS NULL
        ) THEN
            RAISE EXCEPTION 'legacy mcp_access contains users missing from mcp_keys';
        END IF;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM mcp_key_open_kbs new_open
          JOIN mcp_keys new_key ON new_key.id = new_open.key_id
          JOIN knowledge_bases kb ON kb.id = new_open.kb_id
         WHERE kb.domain IS DISTINCT FROM new_key.domain
    ) THEN
        RAISE EXCEPTION 'mcp_key_open_kbs contains cross-domain grants';
    END IF;
END
$$;

DROP TABLE IF EXISTS mcp_open_kbs RESTRICT;
DROP TABLE IF EXISTS mcp_access RESTRICT;

-- Retired ontology review checkpoints: preserve the Run but make it resumable
-- by the generic workflow recovery path before removing the two legacy columns.
UPDATE mining_runs
   SET status = 'interrupted', current_stage = 'mining', pause_step = NULL
 WHERE status = 'awaiting_review';
ALTER TABLE mining_runs DROP COLUMN IF EXISTS subloop_stage;
ALTER TABLE mining_runs DROP COLUMN IF EXISTS ontology_version_id;

DROP TABLE IF EXISTS asset_storage_operations RESTRICT;
DROP TABLE IF EXISTS asset_file_audit_events RESTRICT;
DROP TABLE IF EXISTS asset_storage_object_refs RESTRICT;
DROP TABLE IF EXISTS asset_upload_sessions RESTRICT;
DROP TABLE IF EXISTS asset_storage_quotas RESTRICT;

DROP TABLE IF EXISTS asset_parse_run_attempts RESTRICT;
DROP TABLE IF EXISTS asset_raw_segment_relations RESTRICT;
DROP TABLE IF EXISTS asset_segment_element_links RESTRICT;

DROP TABLE IF EXISTS asset_retrieval_embeddings RESTRICT;
DROP TABLE IF EXISTS asset_retrieval_units RESTRICT;

DROP TABLE IF EXISTS ontology_evidence_nodes RESTRICT;
DROP TABLE IF EXISTS asset_segment_entity_mentions RESTRICT;
DROP TABLE IF EXISTS ontology_candidates RESTRICT;
DROP TABLE IF EXISTS ontology_entity_relations RESTRICT;
DROP TABLE IF EXISTS ontology_alias_dictionary RESTRICT;
DROP TABLE IF EXISTS ontology_entities RESTRICT;
DROP TABLE IF EXISTS ontology_relation_types RESTRICT;
DROP TABLE IF EXISTS ontology_node_types RESTRICT;
DROP TABLE IF EXISTS ontology_versions RESTRICT;

DROP TABLE IF EXISTS asset_publish_releases RESTRICT;
DROP TABLE IF EXISTS serving_query_cache RESTRICT;
