-- 015（A2/A3，39 号 §2.1/§3.1）：章节范围搜索与表格精确消费数据面。
--
-- A2：
--   * asset_retrieval_units_v2(+staging).section_ref —— 单元所属 section 节点
--     ref（物理下推键）；(snapshot_id, section_ref) 索引供 FTS/dense 范围过滤。
--   * asset_structure_nodes(+staging).element_id —— 章节节点的大纲元素锚
--     （mining 预览面 element_id 体系 ↔ serving st_ ref 体系的结构化桥）。
-- A3：
--   * asset_table_cells(+staging) 补 value_type/normalized_value/formula/
--     row_span/column_span/source_span_id（IR TableCell 已有事实的投影）。
--   * asset_structured_assets(+staging).sheet_name —— 多 sheet 工作簿维度。
--
-- 存量行为 NULL（只增语义）；历史补齐走专用重放 CLI（幂等 UPDATE），
-- 不改快照指纹、不发 Build、不失效 ref（A1 纪律同款）。

-- A2：单元章节归属
ALTER TABLE asset_retrieval_units_v2
    ADD COLUMN IF NOT EXISTS section_ref TEXT;
ALTER TABLE asset_retrieval_units_v2_staging
    ADD COLUMN IF NOT EXISTS section_ref TEXT;
CREATE INDEX IF NOT EXISTS idx_aru_v2_section
    ON asset_retrieval_units_v2 (snapshot_id, section_ref);

-- A2：结构节点大纲锚
ALTER TABLE asset_structure_nodes
    ADD COLUMN IF NOT EXISTS element_id TEXT;
ALTER TABLE asset_structure_nodes_staging
    ADD COLUMN IF NOT EXISTS element_id TEXT;

-- A3：表格 cell 类型化事实
ALTER TABLE asset_table_cells
    ADD COLUMN IF NOT EXISTS value_type TEXT,
    ADD COLUMN IF NOT EXISTS normalized_value TEXT,
    ADD COLUMN IF NOT EXISTS formula TEXT,
    ADD COLUMN IF NOT EXISTS row_span INTEGER,
    ADD COLUMN IF NOT EXISTS column_span INTEGER,
    ADD COLUMN IF NOT EXISTS source_span_id TEXT;
ALTER TABLE asset_table_cells_staging
    ADD COLUMN IF NOT EXISTS value_type TEXT,
    ADD COLUMN IF NOT EXISTS normalized_value TEXT,
    ADD COLUMN IF NOT EXISTS formula TEXT,
    ADD COLUMN IF NOT EXISTS row_span INTEGER,
    ADD COLUMN IF NOT EXISTS column_span INTEGER,
    ADD COLUMN IF NOT EXISTS source_span_id TEXT;

-- A3：表格资产 sheet 维度
ALTER TABLE asset_structured_assets
    ADD COLUMN IF NOT EXISTS sheet_name TEXT;
ALTER TABLE asset_structured_assets_staging
    ADD COLUMN IF NOT EXISTS sheet_name TEXT;
