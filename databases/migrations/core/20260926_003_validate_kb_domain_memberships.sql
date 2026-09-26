-- Domain membership is a hard boundary for active non-site-admin KB access.
-- Fail closed and list a bounded sample; operators must repair bindings before retrying.
DO $$
DECLARE
    violation_count BIGINT := 0;
    violation_sample TEXT;
BEGIN
    WITH violations AS (
        SELECT 'owner'::TEXT AS relation_type,
               u.id AS user_id,
               kb.id AS kb_id,
               kb.domain
          FROM knowledge_bases kb
          JOIN kb_users u ON u.id = kb.owner_id
         WHERE kb.status = 'active'
           AND u.status = 'active'
           AND u.site_role <> 'admin'
           AND NOT EXISTS (
               SELECT 1
                 FROM user_domains ud
                WHERE ud.user_id = u.id
                  AND ud.domain = kb.domain
           )
        UNION ALL
        SELECT 'member'::TEXT AS relation_type,
               u.id AS user_id,
               kb.id AS kb_id,
               kb.domain
          FROM kb_members km
          JOIN knowledge_bases kb ON kb.id = km.kb_id
          JOIN kb_users u ON u.id = km.user_id
         WHERE kb.status = 'active'
           AND u.status = 'active'
           AND u.site_role <> 'admin'
           AND NOT EXISTS (
               SELECT 1
                 FROM user_domains ud
                WHERE ud.user_id = u.id
                  AND ud.domain = kb.domain
           )
    ), sampled AS (
        SELECT relation_type, user_id, kb_id, domain,
               count(*) OVER () AS total_count
          FROM violations
         ORDER BY domain, kb_id, relation_type, user_id
         LIMIT 50
    )
    SELECT COALESCE(max(total_count), 0),
           string_agg(
               format('%s:user=%s,kb=%s,domain=%s', relation_type, user_id, kb_id, domain),
               '; ' ORDER BY domain, kb_id, relation_type, user_id
           )
      INTO violation_count, violation_sample
      FROM sampled;

    IF violation_count > 0 THEN
        RAISE EXCEPTION
            'hard domain boundary violation: % active non-site-admin KB grants lack user_domains binding; sample: %',
            violation_count,
            violation_sample
            USING ERRCODE = '23514';
    END IF;
END
$$;
