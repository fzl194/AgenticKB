-- 021 · 快照 MIME 白名单放宽：独立图片（png / jpeg）入库（S3 文件格式支持补全）
--
-- 背景：native_image VLM 解析链上线，独立 png/jpg 图片作为文档入库，
-- 快照 mime_type=frozen.mime（image/png、image/jpeg），白名单需同步放宽。
-- gif 故意不收（68号报告 §六.S3 留白）。
-- 行为：纯放宽（DROP 旧 check + ADD 含全部旧值+2 新值），存量零变更。
-- 幂等：019/020 同款 DROP 循环。

DO $$
DECLARE old_check RECORD;
BEGIN
    FOR old_check IN
        SELECT constraints.conname
        FROM pg_constraint AS constraints
        WHERE constraints.conrelid = 'asset_document_snapshots'::regclass
          AND constraints.contype = 'c'
          AND 'mime_type'::name = ANY(
              SELECT attributes.attname
              FROM unnest(constraints.conkey) AS keys(attnum)
              JOIN pg_attribute AS attributes
                ON attributes.attrelid = constraints.conrelid
               AND attributes.attnum = keys.attnum
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE asset_document_snapshots DROP CONSTRAINT %I',
            old_check.conname
        );
    END LOOP;
END
$$;

ALTER TABLE asset_document_snapshots ADD CONSTRAINT ck_asset_snapshot_mime_type
    CHECK (mime_type IN (
        'application/x-onenet+jsonl',
        'text/markdown', 'text/plain', 'text/html', 'application/pdf',
        'text/csv',
        'application/json',
        'image/png',
        'image/jpeg',
        'application/msword',
        'application/vnd.ms-excel',
        'application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/octet-stream', 'other'
    ));
