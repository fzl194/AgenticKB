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
              LEFT JOIN kb_users old_user ON old_user.id = old_key.user_id
              LEFT JOIN mcp_keys new_key ON new_key.key_hash = old_key.key_hash
             WHERE old_user.id IS NULL
                OR new_key.id IS NULL
                OR new_key.user_id IS DISTINCT FROM old_key.user_id
                OR new_key.key_prefix IS DISTINCT FROM old_key.key_prefix
                OR new_key.status IS DISTINCT FROM old_key.status
                OR new_key.open_tools IS DISTINCT FROM old_key.open_tools
                OR new_key.instructions IS DISTINCT FROM old_key.instructions
                OR new_key.tool_descriptions IS DISTINCT FROM old_key.tool_descriptions
                OR (
                    COALESCE(
                        (SELECT kb.domain
                           FROM mcp_open_kbs old_open
                           JOIN knowledge_bases kb ON kb.id = old_open.kb_id
                          WHERE old_open.user_id = old_key.user_id
                            AND kb.status = 'active'
                          GROUP BY kb.domain
                          ORDER BY count(*) DESC, kb.domain ASC LIMIT 1),
                        (SELECT min(ud.domain) FROM user_domains ud
                          WHERE ud.user_id = old_key.user_id)
                    ) IS NOT NULL
                    AND new_key.domain IS DISTINCT FROM COALESCE(
                        (SELECT kb.domain
                           FROM mcp_open_kbs old_open
                           JOIN knowledge_bases kb ON kb.id = old_open.kb_id
                          WHERE old_open.user_id = old_key.user_id
                            AND kb.status = 'active'
                          GROUP BY kb.domain
                          ORDER BY count(*) DESC, kb.domain ASC LIMIT 1),
                        (SELECT min(ud.domain) FROM user_domains ud
                          WHERE ud.user_id = old_key.user_id)
                    )
                )
        ) THEN
            RAISE EXCEPTION 'legacy mcp_access key identity/config is not preserved in mcp_keys';
        END IF;

        IF to_regclass('public.mcp_open_kbs') IS NOT NULL AND EXISTS (
            SELECT 1
              FROM mcp_open_kbs old_open
              JOIN mcp_access old_key ON old_key.user_id = old_open.user_id
              JOIN mcp_keys new_key ON new_key.key_hash = old_key.key_hash
              JOIN knowledge_bases kb ON kb.id = old_open.kb_id
             WHERE kb.status = 'active'
               AND kb.domain = new_key.domain
               AND NOT EXISTS (
                   SELECT 1 FROM mcp_key_open_kbs new_open
                    WHERE new_open.key_id = new_key.id
                      AND new_open.kb_id = old_open.kb_id
               )
        ) THEN
            RAISE EXCEPTION 'legacy mcp_open_kbs contains same-domain active grants missing from mcp_key_open_kbs';
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
    END IF;
END
$$;

DROP TABLE IF EXISTS mcp_open_kbs RESTRICT;
DROP TABLE IF EXISTS mcp_access RESTRICT;

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
