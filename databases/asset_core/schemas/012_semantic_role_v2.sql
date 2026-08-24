-- Widen the semantic_role CHECK on raw segments and retrieval units for the
-- segment-compiler v2 chapter-pattern roles (definition / enumeration /
-- conclusion / navigation / overview).  Legacy domain vocabulary is kept so
-- historical rows stay valid.  Idempotent: drop old CHECK then re-add with
-- the expanded enum (same pattern as 007_asset_block_type_image).

ALTER TABLE asset_raw_segments
    DROP CONSTRAINT IF EXISTS asset_raw_segments_semantic_role_check;

ALTER TABLE asset_raw_segments
    ADD CONSTRAINT asset_raw_segments_semantic_role_check CHECK (
        semantic_role IN (
            'concept', 'parameter', 'example', 'note', 'procedure_step',
            'troubleshooting_step', 'constraint', 'alarm', 'checklist',
            'definition', 'enumeration', 'conclusion', 'navigation',
            'overview', 'unknown'
        )
    );

ALTER TABLE asset_retrieval_units
    DROP CONSTRAINT IF EXISTS asset_retrieval_units_semantic_role_check;

ALTER TABLE asset_retrieval_units
    ADD CONSTRAINT asset_retrieval_units_semantic_role_check CHECK (
        semantic_role IN (
            'concept', 'parameter', 'example', 'note', 'procedure_step',
            'troubleshooting_step', 'constraint', 'alarm', 'checklist',
            'definition', 'enumeration', 'conclusion', 'navigation',
            'overview', 'unknown'
        )
    );
