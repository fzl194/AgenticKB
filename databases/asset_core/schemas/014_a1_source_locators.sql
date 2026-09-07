-- A1 source locators（37/38 号）：来源记录面——随快照物化的证据精确定位。
--
-- 启动迁移（同 013 纪律）：严禁业务 repository 热路径 DDL；advisory lock
-- 让并发实例串行。locator 是"第四面资产"：输入是 parse bucket 里已有的
-- IR 位置实值（EvidenceSpan 四通道），输出按 representation 粒度对齐
-- asset_retrieval_units_v2——召回热路径不读，hydrate/assemble 按
-- (snapshot_id, representation_id) 点查。
--
-- locator_kind 口径（37 号 NFR 分母规则）：
--   page / line_range / sheet_cell → 已声明可精确定位（进"来源可解析率"分母）
--   native                          → 有原生坐标但非用户可读精确定位（DOCX 段索引/HTML xpath/PPTX slide）
--   section_only                    → 仅 L1（章节）；section/document 级表示
--   unavailable                     → IR 三通道全空（必须有行、不得静默缺失）

SELECT pg_advisory_xact_lock(
    hashtextextended('agentickb:a1-source-locators-schema', 0)
);

CREATE TABLE IF NOT EXISTS asset_source_locators (
    snapshot_id        TEXT NOT NULL,
    representation_id  TEXT NOT NULL,
    target_ref         TEXT NOT NULL,
    document_ref       TEXT NOT NULL,
    source_format      TEXT NOT NULL,
    locator_kind       TEXT NOT NULL,
    section_path       TEXT,
    section_element_id TEXT,
    page               INTEGER,
    line_start         INTEGER,
    line_end           INTEGER,
    sheet              TEXT,
    cell               TEXT,
    table_ref          TEXT,
    row_index          INTEGER,
    native_ref_json    JSONB,
    description        TEXT,
    locator_version    TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, representation_id)
);

CREATE TABLE IF NOT EXISTS asset_source_locators_staging
    (LIKE asset_source_locators INCLUDING ALL);

CREATE INDEX IF NOT EXISTS idx_asset_source_locators_snapshot
    ON asset_source_locators (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_asset_source_locators_staging_snapshot
    ON asset_source_locators_staging (snapshot_id);

-- 指标口径审计视图：分母=声明精确定位的行，分子=按 kind 实际落了对应
-- 字段的行（page→page 非空，line_range→两行号非空，sheet_cell→cell 非空）。
CREATE OR REPLACE VIEW v_source_locator_resolvability AS
SELECT
    source_format,
    locator_kind,
    COUNT(*) AS total,
    COUNT(*) FILTER (
        WHERE (locator_kind = 'page' AND page IS NOT NULL)
           OR (locator_kind = 'line_range' AND line_start IS NOT NULL
                                    AND line_end IS NOT NULL)
           OR (locator_kind = 'sheet_cell' AND cell IS NOT NULL)
    ) AS resolved
FROM asset_source_locators
WHERE locator_kind IN ('page', 'line_range', 'sheet_cell')
GROUP BY source_format, locator_kind;
