-- =============================================================================
-- 011_m5_segment_links_postgresql.sql — M5 切片编译落库（PG）
-- =============================================================================
-- 文档解析平台化 M5（SRS §8.3 asset_raw_segments 增列 + §8.2
-- Segment Element Link 表）。对齐 011_m5_segment_links.sql（SQLite）；
-- 挂链在 010 之后。
-- =============================================================================

ALTER TABLE asset_raw_segments ADD COLUMN IF NOT EXISTS compiler_fingerprint TEXT;

CREATE TABLE IF NOT EXISTS asset_segment_element_links (
    id                  TEXT PRIMARY KEY,
    document_snapshot_id TEXT NOT NULL,               -- M5 补 FK -> asset_document_snapshots(id)
    segment_index       INTEGER NOT NULL,
    element_id          TEXT NOT NULL,
    evidence_span_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
    char_start          INTEGER,
    char_end            INTEGER,
    metadata_json       JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_asset_segment_links_snapshot
    ON asset_segment_element_links(document_snapshot_id, segment_index);

CREATE INDEX IF NOT EXISTS idx_asset_segment_links_element
    ON asset_segment_element_links(document_snapshot_id, element_id);

COMMENT ON TABLE  asset_segment_element_links IS 'SRS §4.12/§8.2: 切片到原文元素/证据 span 的多对多映射——检索命中可回原文定位。';
COMMENT ON COLUMN asset_raw_segments.compiler_fingerprint IS 'M5: 该批切片的编译器+策略指纹（A08：策略变化产生新快照并重切）。';
