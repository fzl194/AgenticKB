-- =============================================================================
-- 011_m5_segment_links_postgresql.sql — M5 切片编译落库（PG）
-- =============================================================================
-- 文档解析平台化 M5（SRS §8.3 asset_raw_segments 增列 + §8.2
-- Segment Element Link 表）。对齐 011_m5_segment_links.sql（SQLite）；
-- 挂链在 010 之后。
-- =============================================================================

ALTER TABLE asset_raw_segments ADD COLUMN IF NOT EXISTS compiler_fingerprint TEXT;

COMMENT ON COLUMN asset_raw_segments.compiler_fingerprint IS 'M5: 该批切片的编译器+策略指纹（A08：策略变化产生新快照并重切）。';
