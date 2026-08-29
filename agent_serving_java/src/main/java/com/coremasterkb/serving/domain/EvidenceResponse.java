package com.coremasterkb.serving.domain;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * 正常检索的唯一公开响应协议（25 号 §5.3，批次8 R6）。
 *
 * <p>硬约束：顶层只有 {@code query/evidence[]/has_more}；evidence item 只有
 * {@code ref/type/content/source/truncated/structure_ref?}；不暴露内部 UUID、retrieval
 * unit/raw segment id、score/rank、边/图、evidence group、范式节点。JSON 字段名按协议
 * snake_case 序列化；可选字段（{@code structure_ref} 与 source 的可选子字段）为 null 时省略。</p>
 *
 * @param query    用户原问题
 * @param evidence 可读证据列表（rerank/RRF 顺序）
 * @param hasMore  assemble 已知还有候选因条数/token 预算未输出（非传统分页语义）
 */
public record EvidenceResponse(
        String query,
        List<EvidenceItem> evidence,
        @JsonProperty("has_more") boolean hasMore
) {

    public EvidenceResponse {
        if (evidence == null) evidence = List.of();
    }

    /**
     * 单条公开证据。{@code ref} 为 opaque、稳定、不可枚举（ev_ 前缀 + HMAC 短哈希）；
     * {@code truncated=true} 表示可用 get_evidence(ref) 取准确完整内容（R8）。
     */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record EvidenceItem(
            String ref,
            String type,
            String content,
            EvidenceSource source,
            boolean truncated,
            @JsonProperty("structure_ref") String structureRef
    ) {}

    /**
     * 来源投影。{@code document_ref} 为 opaque（doc_ 前缀）；{@code relative_path}/
     * {@code section}/{@code page} 可得才返回。
     */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record EvidenceSource(
            @JsonProperty("knowledge_base") String knowledgeBase,
            @JsonProperty("file_name") String fileName,
            @JsonProperty("relative_path") String relativePath,
            @JsonProperty("document_ref") String documentRef,
            String section,
            Integer page
    ) {}
}
