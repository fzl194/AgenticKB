-- 019 · 快照 MIME 白名单放宽：旧二进制 Office（xls/ppt）直存（S1 文件格式支持补全）
--
-- 背景：mime 检查约束（010 建立）只含 application/msword。转换通道泛化后
-- （parse_operator._convert_legacy_office：xls→xlsx、ppt→pptx），快照
-- mime_type=frozen.mime 记录的是**源文档 MIME**（与 .doc 一致语义：
-- msword 直存、转换只影响解析链）。旧二进制 xls/ppt 同样需要直存白名单。
-- 行为：纯放宽（DROP 旧 check + ADD 含全部旧值+2 新值），存量零变更。
-- 幂等：010 的 DROP 循环同款——先删所有针对 mime_type 的 CHECK，再重建。

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
        'application/msword',
        'application/vnd.ms-excel',
        'application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/octet-stream', 'other'
    ));
