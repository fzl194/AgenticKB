package com.coremasterkb.serving.operator.operators.fuse;

import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.domain.ScoreChain;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 唯一 RRF 融合契约（批次8 R3，25 号 §6.6）：canonical 对齐不重复占位、channelWeights 生效、
 * k 默认 60、equal score 稳定 tie-break（最佳名次 → canonical key）。
 */
@DisplayName("RrfSupport")
class RrfSupportTest {

    /** 通道内候选：canonical + channel + rank（模拟 ChannelAggregator 输出）。 */
    private static RetrievalCandidate cand(String canonical, String channel, int rank, double score) {
        return new RetrievalCandidate(
                "rep-" + canonical + "-" + rank, score, channel, Map.of(),
                new ScoreChain(score, 0.0, 0.0, List.of(channel)),
                List.of("rep-" + canonical + "-" + rank), "prose", canonical,
                "segment", canonical, channel, rank, score, "text", Map.of());
    }

    @Test
    @DisplayName("two-channel fusion: shared canonical ranks first with summed score")
    void dualChannelFusion() {
        List<RetrievalCandidate> merged = List.of(
                cand("ev-a", "fts", 1, 0.9),
                cand("ev-b", "fts", 2, 0.8),
                cand("ev-a", "dense", 1, 0.95),
                cand("ev-c", "dense", 2, 0.7));

        List<RetrievalCandidate> out = RrfSupport.fuse(merged, 60, null);

        assertThat(out).hasSize(3);
        assertThat(out.get(0).canonicalEvidenceId()).isEqualTo("ev-a");
        double expected = 1.0 / (60 + 1) + 1.0 / (60 + 1);
        assertThat(out.get(0).score()).isCloseTo(expected, org.assertj.core.data.Offset.offset(1e-12));
        assertThat(out.get(0).scoreChain().routeSources()).containsExactlyInAnyOrder("fts", "dense");
        // 融合只改顺序/score：canonical target 不变
        assertThat(out.get(0).canonicalEvidenceId()).isEqualTo("ev-a");
        assertThat(out.get(0).targetRef()).isEqualTo("ev-a");
    }

    @Test
    @DisplayName("channel weights change the fused order")
    void weightsTakeEffect() {
        List<RetrievalCandidate> merged = List.of(
                cand("ev-a", "fts", 1, 0.9),
                cand("ev-b", "dense", 2, 0.7));

        // Even: ev-a (1/61) vs ev-b (1/62) → ev-a first
        List<RetrievalCandidate> even = RrfSupport.fuse(merged, 60, null);
        assertThat(even.get(0).canonicalEvidenceId()).isEqualTo("ev-a");

        // Weighted: dense×5 → ev-b (5/62) > ev-a (1/61)
        List<RetrievalCandidate> weighted = RrfSupport.fuse(merged, 60, Map.of("dense", 5.0));
        assertThat(weighted.get(0).canonicalEvidenceId()).isEqualTo("ev-b");
        assertThat(weighted.get(0).score()).isCloseTo(5.0 / 62, org.assertj.core.data.Offset.offset(1e-12));
    }

    @Test
    @DisplayName("same canonical from both channels occupies one fused slot (no double voting)")
    void canonicalAlignmentNoDuplication() {
        // fts 命中 ev-1 的两个表示已在通道内聚合为一条（rank=1）；dense 也命中 ev-1。
        List<RetrievalCandidate> merged = List.of(
                cand("ev-1", "fts", 1, 0.9),
                cand("ev-1", "dense", 1, 0.95),
                cand("ev-2", "fts", 2, 0.8));

        List<RetrievalCandidate> out = RrfSupport.fuse(merged, 60, null);

        assertThat(out).hasSize(2);
        assertThat(out.stream().filter(c -> "ev-1".equals(c.canonicalEvidenceId())).count()).isEqualTo(1);
        assertThat(out.get(0).canonicalEvidenceId()).isEqualTo("ev-1");
        assertThat(out.get(0).representationRefs()).hasSize(1); // 通道聚合后的代表，不再二次膨胀
    }

    @Test
    @DisplayName("equal fused score → stable tie-break by best rank then canonical key")
    void equalScoreTieBreakStable() {
        // ev-a: fts rank2 → 1/62; ev-b: dense rank2 → 1/62 (equal), best rank equal → key order
        List<RetrievalCandidate> merged = List.of(
                cand("ev-b", "fts", 2, 0.5),
                cand("ev-a", "dense", 2, 0.5));

        List<RetrievalCandidate> out = RrfSupport.fuse(merged, 60, null);

        assertThat(out).extracting(RetrievalCandidate::canonicalEvidenceId)
                .containsExactly("ev-a", "ev-b");

        // 再跑一次完全相同的输入 → 完全相同的输出（确定性）
        List<RetrievalCandidate> again = RrfSupport.fuse(merged, 60, null);
        assertThat(again).extracting(RetrievalCandidate::canonicalEvidenceId)
                .containsExactly("ev-a", "ev-b");
    }

    @Test
    @DisplayName("equal score, different best rank → better rank first")
    void equalScoreBetterRankFirst() {
        // ev-a best rank 2 (1/62)；ev-b best rank 1 (1/61)？不等分。构造等分：ev-a rank1@fts=1/61,
        // ev-b rank1@densen×…用权重令两分相等：ev-a 1/61≈0.01639, ev-b w/62=0.01639 → w=62/61
        List<RetrievalCandidate> merged = List.of(
                cand("ev-a", "fts", 1, 0.9),
                cand("ev-b", "dense", 2, 0.7));
        double w = 62.0 / 61.0;

        List<RetrievalCandidate> out = RrfSupport.fuse(merged, 60, Map.of("dense", w));

        // 分数几乎相等（浮点），最佳名次 1 < 2 → ev-a 稳定在前
        assertThat(Math.abs(out.get(0).score() - out.get(1).score())).isLessThan(1e-9);
        assertThat(out.get(0).canonicalEvidenceId()).isEqualTo("ev-a");
    }

    @Test
    @DisplayName("legacy candidates (no canonical/channel) still fuse via unit id + source")
    void legacyCandidatesSupported() {
        List<RetrievalCandidate> legacy = List.of(
                new RetrievalCandidate("u1", 0.9, "x", Map.of(),
                        new ScoreChain(0.9, 0, 0, List.of("x"))),
                new RetrievalCandidate("u2", 0.8, "x", Map.of(),
                        new ScoreChain(0.8, 0, 0, List.of("x"))),
                new RetrievalCandidate("u2", 0.95, "y", Map.of(),
                        new ScoreChain(0.95, 0, 0, List.of("y"))));

        List<RetrievalCandidate> out = RrfSupport.fuse(legacy, 60, null);

        assertThat(out).hasSize(2);
        assertThat(out.get(0).retrievalUnitId()).isEqualTo("u2");
    }

    @Test
    @DisplayName("empty input → empty output; no DB, no threshold filtering")
    void emptyInput() {
        assertThat(RrfSupport.fuse(List.of(), 60, null)).isEmpty();
        assertThat(RrfSupport.fuse(null, 60, null)).isEmpty();
    }
}
