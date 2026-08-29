package com.coremasterkb.serving.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Tests that all domain records provide correct null defaults.
 */
@DisplayName("Domain Record Defaults")
class DomainRecordDefaultsTest {

    @Nested
    @DisplayName("SearchRequest")
    class SearchRequestTests {
        @Test
        @DisplayName("null scope defaults to empty map")
        void nullScopeDefaultsToEmptyMap() {
            var req = new SearchRequest("test", null, null, false, "cloud_core_network", null, null);
            assertThat(req.scope()).isEmpty();
            assertThat(req.entities()).isEmpty();
            assertThat(req.mode()).isEqualTo("evidence");
        }
    }

    @Nested
    @DisplayName("ActiveScope")
    class ActiveScopeTests {
        @Test
        @DisplayName("null collections default to empty")
        void nullCollectionsDefaultToEmpty() {
            var scope = new ActiveScope("rel", "build", null, null);
            assertThat(scope.snapshotIds()).isEmpty();
            assertThat(scope.documentSnapshotMap()).isEmpty();
        }
    }

    @Nested
    @DisplayName("ScoreChain")
    class ScoreChainTests {
        @Test
        @DisplayName("null routeSources defaults to empty list")
        void nullRouteSourcesDefaultsToEmptyList() {
            var chain = new ScoreChain(0.5, 0.3, 0.4, null);
            assertThat(chain.routeSources()).isEmpty();
        }
    }

    @Nested
    @DisplayName("RetrievalCandidate")
    class RetrievalCandidateTests {
        @Test
        @DisplayName("null metadata defaults to empty map")
        void nullMetadataDefaultsToEmptyMap() {
            var cand = new RetrievalCandidate("unit1", 0.9, "bm25", null, null);
            assertThat(cand.metadata()).isEmpty();
        }
    }

    @Nested
    @DisplayName("QueryUnderstanding")
    class QueryUnderstandingTests {
        @Test
        @DisplayName("null fields default correctly")
        void nullFieldsDefaultCorrectly() {
            var qu = new QueryUnderstanding("q", null, null, null, null, null, null, null, null, null);
            assertThat(qu.intent()).isEqualTo("general");
            assertThat(qu.subQueries()).isEmpty();
            assertThat(qu.entities()).isEmpty();
            assertThat(qu.scope()).isEmpty();
            assertThat(qu.keywords()).isEmpty();
            assertThat(qu.evidenceNeed()).isNotNull();
            assertThat(qu.ambiguities()).isEmpty();
            assertThat(qu.source()).isEqualTo("rule");
        }
    }

    @Nested
    @DisplayName("SubQuery")
    class SubQueryTests {
        @Test
        @DisplayName("null fields default correctly")
        void nullFieldsDefaultCorrectly() {
            var sq = new SubQuery("text", null, null);
            assertThat(sq.intent()).isEqualTo("general");
            assertThat(sq.entities()).isEmpty();
        }
    }

    @Nested
    @DisplayName("EntityRef")
    class EntityRefTests {
        @Test
        @DisplayName("null fields default to empty string")
        void nullFieldsDefaultToEmpty() {
            var ref = new EntityRef(null, "SMF", null);
            assertThat(ref.type()).isEmpty();
            assertThat(ref.normalizedName()).isEmpty();
        }
    }

    @Nested
    @DisplayName("SourceRef")
    class SourceRefTests {
        @Test
        @DisplayName("null maps default to empty")
        void nullMapsDefaultToEmpty() {
            var ref = new SourceRef("id", "key", "title", "path", null, null);
            assertThat(ref.scopeJson()).isEmpty();
            assertThat(ref.metadata()).isEmpty();
        }
    }

    @Nested
    @DisplayName("EvidenceNeed")
    class EvidenceNeedTests {
        @Test
        @DisplayName("empty factory returns correct defaults")
        void emptyFactory() {
            var need = EvidenceNeed.empty();
            assertThat(need.preferredRoles()).isEmpty();
            assertThat(need.preferredBlocks()).isEmpty();
            assertThat(need.needsComparison()).isFalse();
            assertThat(need.needsCitation()).isFalse();
        }
    }

    @Nested
    @DisplayName("RetrievalQuery")
    class RetrievalQueryTests {
        @Test
        @DisplayName("null fields default correctly")
        void nullFieldsDefaultCorrectly() {
            var query = new RetrievalQuery("q", null, null, null, null, null, null, null);
            assertThat(query.keywords()).isEmpty();
            assertThat(query.entities()).isEmpty();
            assertThat(query.subQueries()).isEmpty();
            assertThat(query.intent()).isEqualTo("general");
            assertThat(query.scope()).isEmpty();
        }
    }

    @Nested
    @DisplayName("HydratedEvidence (批次8 R5)")
    class HydratedEvidenceTests {
        @Test
        @DisplayName("null collections default to empty; contentText joins fragments in order")
        void nullCollectionsDefaultToEmpty() {
            var evidence = new HydratedEvidence("snap", "doc:/a#seg:1", "segment",
                    "doc:/a#seg:1", "prose", "doc:/a", null, 1, 1, 1,
                    null, null, null, false, false, 0, null, null);
            assertThat(evidence.orderedFragments()).isEmpty();
            assertThat(evidence.structureRefs()).isEmpty();
            assertThat(evidence.provenance()).isEmpty();
            assertThat(evidence.contentText()).isEmpty();
        }

        @Test
        @DisplayName("contentText joins fragments with newline, preserving order")
        void contentTextJoinsInOrder() {
            var evidence = new HydratedEvidence("snap", "c", "segment", "r", "prose",
                    "doc:/a", null, 1, 1, 1,
                    List.of(new HydratedEvidence.EvidenceFragment("window", "前文", null, null, null),
                            new HydratedEvidence.EvidenceFragment("exact", "命中", null, null, null)),
                    "window", List.of(), false, false, 0, null, Map.of());
            assertThat(evidence.contentText()).isEqualTo("前文\n命中");
        }
    }

    @Nested
    @DisplayName("EvidenceResponse (批次8 R6)")
    class EvidenceResponseTests {
        @Test
        @DisplayName("null evidence defaults to empty list")
        void nullEvidenceDefaultsToEmpty() {
            var resp = new EvidenceResponse("q", null, false);
            assertThat(resp.evidence()).isEmpty();
            assertThat(resp.hasMore()).isFalse();
        }
    }
}
