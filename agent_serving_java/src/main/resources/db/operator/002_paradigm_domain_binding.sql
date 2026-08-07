-- =============================================================================
-- Paradigm → domain binding (MCP auto-matching, see docs/mcp-paradigm-routing-design.md)
-- Lives in the same control DB as 001. Idempotent — applied on startup by
-- ParadigmSchemaInitializer, which names its scripts EXPLICITLY: adding a file to this
-- directory does nothing until it is registered there.
--
-- Binding is MUTABLE paradigm metadata and is deliberately NOT part of
-- operator_paradigm_version: versions stay immutable graph snapshots, so a
-- (paradigmId, version) call always replays identically. Re-binding creates no new version.
-- =============================================================================

-- NULL = unbound: still callable via /{id}/search, just excluded from auto-matching.
-- That is what keeps the test/eval paradigms (the `collect`-terminated ones) working untouched.
ALTER TABLE operator_paradigm ADD COLUMN IF NOT EXISTS bound_domain VARCHAR(64);

ALTER TABLE operator_paradigm ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE operator_paradigm ADD COLUMN IF NOT EXISTS bound_at TIMESTAMP;

-- "At most one live default paradigm per domain" — structurally identical to the partial unique
-- index on asset_publish_releases WHERE status='active'.
--
-- Two consequences worth knowing before you write code against this:
--   1. Switching the default MUST clear the old row before setting the new one, in ONE
--      transaction. Set-then-clear raises 23505. (Mirrors activate_release().)
--   2. status='active' is part of the predicate, so archiving a paradigm automatically
--      releases the domain's default slot — no explicit unbind needed.
--
-- This index also fully serves the resolve lookup
-- (WHERE bound_domain=? AND is_default AND status='active'), so no separate index is needed.
CREATE UNIQUE INDEX IF NOT EXISTS uq_paradigm_domain_default
    ON operator_paradigm (bound_domain)
    WHERE is_default AND status = 'active' AND bound_domain IS NOT NULL;
