-- 020 · 快照 MIME 白名单放宽：csv / json 直存（S2 文件格式支持补全）
--
-- 背景：native_csv / native_json 解析链上线，快照 mime_type=frozen.mime
-- 记录源文档 MIME（text/csv、application/json），白名单需同步放宽。
-- 行为：纯放宽（DROP 旧 check + ADD 含全部旧值+2 新值），存量零变更。
-- 幂等：019 同款 DROP 循环。

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
        'application/msword',
        'application/vnd.ms-excel',
        'application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/octet-stream', 'other'
    ));
