package com.coremasterkb.serving.operator.operators.retrieve;

import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.domain.ScoreChain;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import com.coremasterkb.serving.util.JsonUtils;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 通道内 canonical 聚合（批次8 R2，25 号 §5.1/§6.4/§6.5）。
 *
 * <p>同一召回通道（fts/dense）的 SQL 命中按 {@code canonical_evidence_id} 聚合：同 canonical
 * 的多表示（raw/query alias/summary/多视图）只保留 rank 最佳者，不重复占位；候选保留窗口内
 * 全部命中 representation id 列表（provenance）与最佳行的类型/target 契约字段。
 * {@code channelRank} = 聚合后通道内名次（1 基），{@code channelScore} = 最佳行原始分。</p>
 *
 * <p>rank 语义：调用方传入的 rows 必须已按通道相关性降序排好（fts=ts_rank，dense=cosine）；
 * 聚合顺序即名次顺序。窗口内同 canonical 的后续命中不再占用名次。</p>
 */
public final class ChannelAggregator {

    /** ranking_text 有界截断（字符）：reranker 输入预算，不等于最终 evidence content。 */
    public static final int RANKING_TEXT_MAX_CHARS = 2048;

    private ChannelAggregator() {}

    /**
     * 聚合一个通道的命中行。
     *
     * @param rows      已按通道分数降序的 v2 行（窗口内未去重）
     * @param channelId 稳定通道标识（"fts"/"dense"）
     * @param topK      聚合后保留的 canonical 数量
     * @return canonical 聚合后的候选列表（顺序 = 通道内名次）
     */
    public static List<RetrievalCandidate> aggregate(List<UnitV2Row> rows, String channelId, int topK) {
        if (rows == null || rows.isEmpty() || topK <= 0) {
            return List.of();
        }

        // canonical → 聚合态（首个命中行 = 最佳，因 rows 已按分数降序）
        Map<String, Agg> byCanonical = new LinkedHashMap<>();
        for (UnitV2Row row : rows) {
            String canonical = canonicalKey(row);
            Agg agg = byCanonical.get(canonical);
            if (agg == null) {
                agg = new Agg(row);
                byCanonical.put(canonical, agg);
            }
            agg.representationRefs.add(row.getRepresentationId());
        }

        List<RetrievalCandidate> out = new ArrayList<>(Math.min(topK, byCanonical.size()));
        int rank = 0;
        for (Agg agg : byCanonical.values()) {
            rank++;
            if (rank > topK) break;
            out.add(toCandidate(agg, channelId, rank));
        }
        return out;
    }

    /**
     * ranking_text 有界构造（25 号 §6.7）：structural_context（标题面包屑/表头/caption）+
     * content_text，统一拼接，2048 字符截断。alias 型表示的 content_text 本就是源证据摘要，
     * 满足"alias 必须补源证据摘要"的排序输入要求。
     */
    public static String rankingText(String structuralContext, String contentText) {
        String context = structuralContext == null ? "" : structuralContext.trim();
        String content = contentText == null ? "" : contentText.trim();
        String text = context.isEmpty() ? content
                : content.isEmpty() ? context
                : context + "\n" + content;
        return text.length() > RANKING_TEXT_MAX_CHARS
                ? text.substring(0, RANKING_TEXT_MAX_CHARS)
                : text;
    }

    private static String canonicalKey(UnitV2Row row) {
        String canonical = row.getCanonicalEvidenceId();
        return canonical == null || canonical.isEmpty()
                ? row.getRepresentationId() : canonical;
    }

    private static RetrievalCandidate toCandidate(Agg agg, String channelId, int rank) {
        UnitV2Row best = agg.best;
        double score = best.getChannelScore();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("snapshot_id", best.getSnapshotId());
        metadata.put("container_ref", best.getContainerRef());
        metadata.put("ordinal", best.getOrdinal());

        ScoreChain chain = new ScoreChain(score, 0.0, 0.0, List.of(channelId));
        return new RetrievalCandidate(
                best.getRepresentationId(),
                score,
                channelId,
                metadata,
                chain,
                List.copyOf(agg.representationRefs),
                best.getRepresentationType(),
                canonicalKey(best),
                best.getTargetType(),
                best.getTargetRef(),
                channelId,
                rank,
                score,
                rankingText(best.getStructuralContext(), best.getContentText()),
                JsonUtils.safeJsonParse(best.getFacetsJson()));
    }

    private static final class Agg {
        final UnitV2Row best;
        final Set<String> representationRefs = new LinkedHashSet<>();

        Agg(UnitV2Row best) {
            this.best = best;
        }
    }
}
