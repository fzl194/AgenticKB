package com.coremasterkb.serving.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Tests that with* methods on records return new instances (immutability).
 */
@DisplayName("Domain Record With* Methods — Immutability")
class DomainRecordWithMethodsTest {

    @Nested
    @DisplayName("ScoreChain")
    class ScoreChainWithTests {
        @Test
        @DisplayName("withFusionScore returns new instance")
        void withFusionScoreReturnsNew() {
            var original = new ScoreChain(0.9, 0.0, 0.0, List.of("bm25"));
            var modified = original.withFusionScore(0.5);
            assertThat(original.fusionScore()).isEqualTo(0.0);
            assertThat(modified.fusionScore()).isEqualTo(0.5);
            assertThat(modified.rawScore()).isEqualTo(0.9);
            assertThat(modified.routeSources()).containsExactly("bm25");
        }

        @Test
        @DisplayName("withRouteSources returns new instance")
        void withRouteSourcesReturnsNew() {
            var original = new ScoreChain(0.9, 0.5, 0.3, List.of("bm25"));
            var modified = original.withRouteSources(List.of("bm25", "dense"));
            assertThat(original.routeSources()).containsExactly("bm25");
            assertThat(modified.routeSources()).containsExactly("bm25", "dense");
        }
    }

    @Nested
    @DisplayName("RetrievalCandidate")
    class RetrievalCandidateWithTests {
        private final ScoreChain chain = new ScoreChain(0.9, 0.5, 0.3, List.of("bm25"));
        private final RetrievalCandidate original = new RetrievalCandidate("u1", 0.9, "bm25", Map.of("k", "v"), chain);

        @Test
        @DisplayName("withScore returns new instance, original unchanged")
        void withScoreReturnsNew() {
            var modified = original.withScore(0.1);
            assertThat(original.score()).isEqualTo(0.9);
            assertThat(modified.score()).isEqualTo(0.1);
            assertThat(modified.retrievalUnitId()).isEqualTo("u1");
        }

        @Test
        @DisplayName("withSource returns new instance, original unchanged")
        void withSourceReturnsNew() {
            var modified = original.withSource("dense");
            assertThat(original.source()).isEqualTo("bm25");
            assertThat(modified.source()).isEqualTo("dense");
        }

        @Test
        @DisplayName("withScoreChain returns new instance, original unchanged")
        void withScoreChainReturnsNew() {
            var newChain = new ScoreChain(0.8, 0.6, 0.4, List.of("dense"));
            var modified = original.withScoreChain(newChain);
            assertThat(original.scoreChain().rawScore()).isEqualTo(0.9);
            assertThat(modified.scoreChain().rawScore()).isEqualTo(0.8);
        }
    }
}
