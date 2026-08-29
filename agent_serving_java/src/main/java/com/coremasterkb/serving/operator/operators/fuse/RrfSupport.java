package com.coremasterkb.serving.operator.operators.fuse;

import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.domain.ScoreChain;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Reciprocal Rank Fusion（批次8 R3 重写，25 号 §6.6）。
 *
 * <p>与旧实现的差异：融合对齐键从表示 id 改为 {@code canonical_evidence_id}（缺失时回退表示
 * id）——同 canonical 的多表示命中（raw/query alias/summary）只占一个融合位；通道由候选的
 * {@code channelId} 固定声明（缺失回退 {@code source}）；通道内名次用候选自带的
 * {@code channelRank}（通道聚合已赋值，1 基），未赋值时按输入顺序内派生。等分 tie-break：
 * 最佳通道名次升序 → canonical key 字典序，输出确定稳定。</p>
 *
 * <p>纯内存确定性计算：无 DB、无文本相似度、无阈值过滤。</p>
 */
final class RrfSupport {

    private RrfSupport() {}

    static List<RetrievalCandidate> fuse(
            List<RetrievalCandidate> candidates, int k, Map<String, Double> channelWeights) {

        if (candidates == null || candidates.isEmpty()) {
            return List.of();
        }
        Map<String, Double> weights = channelWeights != null ? channelWeights : Map.of();

        // 1) 按通道分组；组内确定名次（自带 channelRank 优先，否则按输入顺序）。
        Map<String, List<Ranked>> byChannel = new LinkedHashMap<>();
        int fallbackOrder = 0;
        for (RetrievalCandidate c : candidates) {
            String channel = channelOf(c);
            int rank = c.channelRank() > 0 ? c.channelRank() : ++fallbackOrder;
            byChannel.computeIfAbsent(channel, ch -> new ArrayList<>()).add(new Ranked(c, rank));
        }

        // 2) score(canonical) = Σ weight[channel] / (k + rank)；对齐键 = canonical（回退表示 id）。
        Map<String, Fused> fused = new LinkedHashMap<>();
        for (var entry : byChannel.entrySet()) {
            String channel = entry.getKey();
            double weight = weights.getOrDefault(channel, 1.0);
            for (Ranked r : entry.getValue()) {
                String key = r.candidate.fusionKey();
                Fused f = fused.computeIfAbsent(key, key0 -> {
                    Fused nf = new Fused();
                    nf.key = key0;
                    return nf;
                });
                f.score += weight / (k + r.rank);
                f.channels.add(channel);
                // 代表候选 = 跨通道最佳名次命中（tie 时保留先见者——输入顺序稳定）。
                if (f.best == null || r.rank < f.bestRank) {
                    f.best = r.candidate;
                    f.bestRank = r.rank;
                }
            }
        }

        // 3) 排序：融合分降序 → 最佳通道名次升序 → canonical key 字典序（稳定 tie-break）。
        List<Fused> ordered = new ArrayList<>(fused.values());
        ordered.sort(Comparator
                .comparingDouble((Fused f) -> f.score).reversed()
                .thenComparingInt(f -> f.bestRank)
                .thenComparing(f -> f.key));

        List<RetrievalCandidate> out = new ArrayList<>(ordered.size());
        for (Fused f : ordered) {
            List<String> channels = List.copyOf(f.channels);
            ScoreChain chain = f.best.scoreChain() != null
                    ? f.best.scoreChain()
                    : new ScoreChain(f.best.channelScore(), 0.0, 0.0, channels);
            chain = chain.withFusionScore(f.score).withRouteSources(channels);
            out.add(f.best.withScore(f.score).withScoreChain(chain));
        }
        return out;
    }

    /** 通道标识：候选 channelId（§5.1 固定声明）；旧候选回退 source。 */
    private static String channelOf(RetrievalCandidate c) {
        String channelId = c.channelId();
        return channelId != null && !channelId.isEmpty() ? channelId : c.source();
    }

    private record Ranked(RetrievalCandidate candidate, int rank) {}

    private static final class Fused {
        String key;
        double score;
        int bestRank = Integer.MAX_VALUE;
        RetrievalCandidate best;
        final Set<String> channels = new LinkedHashSet<>();
    }
}
