-- Allow block_type='image' on raw segments and retrieval units (PDF figure dump).
-- Idempotent: drop old CHECK then re-add with the expanded enum.

ALTER TABLE asset_raw_segments
    DROP CONSTRAINT IF EXISTS asset_raw_segments_block_type_check;

ALTER TABLE asset_raw_segments
    ADD CONSTRAINT asset_raw_segments_block_type_check CHECK (
        block_type IN (
            'paragraph', 'heading', 'table', 'list', 'code', 'blockquote',
            'html_table', 'raw_html', 'image', 'unknown'
        )
    );

ALTER TABLE asset_retrieval_units
    DROP CONSTRAINT IF EXISTS asset_retrieval_units_block_type_check;

ALTER TABLE asset_retrieval_units
    ADD CONSTRAINT asset_retrieval_units_block_type_check CHECK (
        block_type IN (
            'paragraph', 'heading', 'table', 'list', 'code', 'blockquote',
            'html_table', 'raw_html', 'image', 'unknown'
        )
    );
