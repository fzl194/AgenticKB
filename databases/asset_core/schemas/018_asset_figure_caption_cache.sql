-- 018（图片VLM图注·方案B）：图注缓存表（fresh install 路径；与存量库版本化
-- 迁移 core/20261124_013_asset_figure_caption_cache 同文，改表必须双改并刷新
-- 镜像校验和）。自包含、身份列无 FK（对齐 kb 表惯例）。
-- 只缓存 VLM 成功图注：键 = sha256(domain|source_raw_hash|parser_fingerprint|
-- figure_element_id|model|prompt_version)（figure_caption/keys.py 单一事实来源），
-- 任一成分变化必换键，天然防串。失败不落缓存（重试有机会）。
CREATE TABLE IF NOT EXISTS asset_figure_caption_cache (
    cache_key           TEXT PRIMARY KEY,
    domain              TEXT NOT NULL,
    source_raw_hash     TEXT NOT NULL,
    parser_fingerprint  TEXT NOT NULL,
    figure_element_id   TEXT NOT NULL,
    model               TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,
    caption             TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE asset_figure_caption_cache IS
    'VLM 图片图注缓存（方案B）：同图重跑零成本；键成分见 figure_caption/keys.py';
CREATE INDEX IF NOT EXISTS idx_asset_figure_caption_cache_source
    ON asset_figure_caption_cache(source_raw_hash, figure_element_id);
