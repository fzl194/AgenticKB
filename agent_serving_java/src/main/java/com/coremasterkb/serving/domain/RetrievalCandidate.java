package com.coremasterkb.serving.domain;

import java.util.List;
import java.util.Map;

/**
 * A single candidate result from the retrieval pipeline (25 号 §5.1 clean-break 契约).
 * Immutable: with* methods return new instances.
 *
 * <p>批次8 R2 起候选携带通道聚合契约字段：同一召回通道（fts/dense）内部先按
 * {@code canonical_evidence_id} 聚合——同 canonical 的多表示命中只保留 rank 最佳者
 * （{@code channelRank} = 通道内最佳名次，1 基），{@code representationRefs} 保留窗口内
 * 全部命中表示 id 供 provenance。融合（rrf）按 canonical 对齐，alias 不重复占位。</p>
 *
 * @param retrievalUnitId    best-hit representation id（内部主键；= representationRefs[0] 的来源表示）
 * @param score              current score
 * @param source             route or stage that produced this candidate（兼容字段：与 channelId 同值）
 * @param metadata           arbitrary metadata; defaults to empty map
 * @param scoreChain         score progression through the pipeline
 * @param representationRefs 命中的搜索表示 id 列表（provenance）；默认空列表
 * @param representationType 命中表示类型（prose/table_row/section/…）
 * @param canonicalEvidenceId 跨 raw/query alias/summary/multi-view 的融合键
 * @param targetType         hydrate 的 canonical target 类型
 * @param targetRef          hydrate 的 canonical target 引用
 * @param channelId          稳定通道标识（"fts"/"dense"）
 * @param channelRank        通道内 canonical 聚合后的最佳名次（1 基；0 = 未赋值）
 * @param channelScore       通道内最佳原始分（ts_rank / cosine）
 * @param rankingText        reranker 有界输入（≠最终 evidence content），构造见 ChannelAggregator
 * @param facets             已持久化的过滤事实（facets_json）；默认空 map
 */
public record RetrievalCandidate(
        String retrievalUnitId,
        double score,
        String source,
        Map<String, Object> metadata,
        ScoreChain scoreChain,
        List<String> representationRefs,
        String representationType,
        String canonicalEvidenceId,
        String targetType,
        String targetRef,
        String channelId,
        int channelRank,
        double channelScore,
        String rankingText,
        Map<String, Object> facets
) {

    public RetrievalCandidate {
        if (metadata == null) metadata = Map.of();
        if (representationRefs == null) representationRefs = List.of();
        if (facets == null) facets = Map.of();
    }

    /** Legacy 5-arg shape (pre-R2 producers: 旧固定链 retriever / tests) — 新契约字段取空默认。 */
    public RetrievalCandidate(
            String retrievalUnitId, double score, String source,
            Map<String, Object> metadata, ScoreChain scoreChain) {
        this(retrievalUnitId, score, source, metadata, scoreChain,
                List.of(), null, null, null, null, source, 0, 0.0, null, Map.of());
    }

    /** 融合对齐键：canonical 优先，旧候选（无 canonical）回退表示 id，避免 null 键互撞。 */
    public String fusionKey() {
        return canonicalEvidenceId != null && !canonicalEvidenceId.isEmpty()
                ? canonicalEvidenceId : retrievalUnitId;
    }

    public RetrievalCandidate withScore(double newScore) {
        return new RetrievalCandidate(retrievalUnitId, newScore, source, metadata, scoreChain,
                representationRefs, representationType, canonicalEvidenceId, targetType, targetRef,
                channelId, channelRank, channelScore, rankingText, facets);
    }

    public RetrievalCandidate withSource(String newSource) {
        return new RetrievalCandidate(retrievalUnitId, score, newSource, metadata, scoreChain,
                representationRefs, representationType, canonicalEvidenceId, targetType, targetRef,
                channelId, channelRank, channelScore, rankingText, facets);
    }

    public RetrievalCandidate withScoreChain(ScoreChain newScoreChain) {
        return new RetrievalCandidate(retrievalUnitId, score, source, metadata, newScoreChain,
                representationRefs, representationType, canonicalEvidenceId, targetType, targetRef,
                channelId, channelRank, channelScore, rankingText, facets);
    }
}
