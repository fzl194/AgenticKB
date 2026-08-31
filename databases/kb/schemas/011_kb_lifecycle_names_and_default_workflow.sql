-- KB lifecycle/name scope and Batch-8 default mining workflow.

SELECT pg_advisory_xact_lock(
    hashtextextended('agentickb:kb-lifecycle-name-scope-v1', 0)
);

ALTER TABLE knowledge_bases
    DROP CONSTRAINT IF EXISTS knowledge_bases_domain_name_key;

-- Do not silently rename existing user data.  Stop migration with a useful
-- error if case/whitespace variants already collide in the intended scope.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM knowledge_bases
        WHERE status = 'active'
        GROUP BY owner_id, domain, lower(btrim(name))
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'active KB names collide after trim/case normalization; resolve duplicates before migration';
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_bases_owner_domain_active_name
    ON knowledge_bases (owner_id, domain, lower(btrim(name)))
    WHERE status = 'active';

UPDATE knowledge_bases
SET mining_workflow_id = 'system-hybrid-assets',
    updated_at = COALESCE(updated_at, created_at)
WHERE mining_workflow_id IS NULL
   OR mining_workflow_id = 'system-full-baseline';
