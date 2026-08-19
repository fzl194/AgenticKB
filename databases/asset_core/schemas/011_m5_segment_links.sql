-- =============================================================================
-- 011_m5_segment_links.sql — M5 切片编译落库（SQLite）
-- =============================================================================
-- 文档解析平台化 M5（SRS §8.3 asset_raw_segments 增列 + §8.2
-- Segment Element Link 表）。ADR-0003 D-003 / D-004（增量幂等）。
--
-- 内容：
--   1. asset_raw_segments 增列：compiler_fingerprint（切片策略指纹，
--      A08：策略变化 → 新快照，本列记录该批切片由哪套策略产出）。
--   2. 新表 asset_segment_element_links：切片 ↔ 原文元素/证据 span 的
--      多对多映射（SRS §4.12「为每个 segment 保存到 Element/Evidence
--      Span 的多对多映射」）。
-- =============================================================================

ALTER TABLE asset_raw_segments ADD COLUMN IF NOT EXISTS compiler_fingerprint TEXT;

CREATE TABLE IF NOT EXISTS asset_segment_element_links (
    id                  TEXT PRIMARY KEY,
    document_snapshot_id TEXT NOT NULL REFERENCES asset_document_snapshots(id) ON DELETE CASCADE,
    segment_index       INTEGER NOT NULL,
    element_id          TEXT NOT NULL,
    evidence_span_ids   TEXT NOT NULL DEFAULT '[]',   -- JSON array of span ids
    char_start          INTEGER,
    char_end            INTEGER,
    metadata_json       TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_asset_segment_links_snapshot
    ON asset_segment_element_links(document_snapshot_id, segment_index);

-- 对抗评审 MEDIUM-8：重复编译/重放不得产生重复 link 行。
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_segment_links_row
    ON asset_segment_element_links(
        document_snapshot_id, segment_index, element_id, char_start);

CREATE INDEX IF NOT EXISTS idx_asset_segment_links_element
    ON asset_segment_element_links(document_snapshot_id, element_id);
