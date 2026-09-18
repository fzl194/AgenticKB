-- =============================================================================
-- 011_m5_segment_links.sql — M5 切片编译落库（SQLite）
-- =============================================================================
-- 文档解析平台化 M5（SRS §8.3 asset_raw_segments 增列）。
-- ADR-0003 D-003 / D-004（增量幂等）。
--
-- 内容：
--   1. asset_raw_segments 增列：compiler_fingerprint（切片策略指纹，
--      A08：策略变化 → 新快照，本列记录该批切片由哪套策略产出）。
--   2. 元素/证据 span 映射保留在 asset_raw_segments.source_offsets_json。
-- =============================================================================

ALTER TABLE asset_raw_segments ADD COLUMN IF NOT EXISTS compiler_fingerprint TEXT;
