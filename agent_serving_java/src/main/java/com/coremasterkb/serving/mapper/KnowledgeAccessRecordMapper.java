package com.coremasterkb.serving.mapper;

import com.coremasterkb.serving.entity.KnowledgeAccessRecord;
import com.coremasterkb.serving.entity.KnowledgeAccessRecordPayload;

public interface KnowledgeAccessRecordMapper {
    int insert(KnowledgeAccessRecord record);

    void upsertPayload(KnowledgeAccessRecordPayload payload);
}
