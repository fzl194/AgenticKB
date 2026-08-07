package com.coremasterkb.serving.application;

import com.coremasterkb.serving.domain.*;
import com.coremasterkb.serving.mapper.result.ExpandedSegmentRow;
import com.coremasterkb.serving.mapper.result.SegmentWithMetaRow;
import com.coremasterkb.serving.repository.AssetRepository;
import com.coremasterkb.serving.retrieval.GraphExpander;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@DisplayName("ContextAssembler")
class ContextAssemblerTest {

    private AssetRepository repo;
    private GraphExpander graphExpander;
    private ContextAssembler assembler;

    @BeforeEach
    void setUp() {
        repo = mock(AssetRepository.class);
        graphExpander = mock(GraphExpander.class);
        assembler = new ContextAssembler(repo, graphExpander);
    }

    @Nested
    @DisplayName("empty candidates")
    class EmptyCandidates {
        @Test
        @DisplayName("empty candidates produces no_result issue")
        void emptyProducesNoResultIssue() {
            var understanding = new QueryUnderstanding("xyzzy123", "general", null, null, null, null,
                    EvidenceNeed.empty(), null, "rule", null);
            var scope = new ActiveScope("rel", "build", List.of("snap1"), Map.of());
            var plan = new RetrievalRoutePlan(null, null, null, null, null, null);

            when(repo.resolveSegmentsByIds(any(), any())).thenReturn(List.of());
            when(repo.getDocumentSources(any(), any())).thenReturn(List.of());

            var pack = assembler.assemble("xyzzy123", understanding, scope, List.of(), plan);
            assertThat(pack.issues()).isNotEmpty();
            assertThat(pack.issues().get(0).type()).isEqualTo("no_result");
            assertThat(pack.items()).isEmpty();
        }
    }

    @Nested
    @DisplayName("low score candidates")
    class LowScoreCandidates {
        @Test
        @DisplayName("all low scores produces low_confidence issue")
        void lowScoreProducesLowConfidenceIssue() {
            var understanding = new QueryUnderstanding("模糊查询", "general", null, null, null, null,
                    EvidenceNeed.empty(), null, "rule", null);
            var scope = new ActiveScope("rel", "build", List.of("snap1"), Map.of());
            var plan = new RetrievalRoutePlan(null, null, null, null,
                    new AssemblyConfig(false, false, 10, 10, 2, List.of()), null);

            var candidate = new RetrievalCandidate("u1", 0.05, "bm25",
                    Map.of("text", "some text"), null);

            when(repo.resolveSegmentsByIds(any(), any())).thenReturn(List.of());
            when(repo.getRelationsForSegments(any(), any(), any())).thenReturn(List.of());
            when(repo.getDocumentSources(any(), any())).thenReturn(List.of());

            var pack = assembler.assemble("模糊查询", understanding, scope, List.of(candidate), plan);
            assertThat(pack.issues()).isNotEmpty();
            assertThat(pack.issues().get(0).type()).isEqualTo("low_confidence");
        }
    }

    @Nested
    @DisplayName("normal candidates")
    class NormalCandidates {
        @Test
        @DisplayName("candidates produce seed items")
        void candidatesProduceSeedItems() {
            var understanding = new QueryUnderstanding("SMF配置", "concept_lookup", null, null, null, null,
                    EvidenceNeed.empty(), null, "rule", null);
            var scope = new ActiveScope("rel", "build", List.of("snap1"), Map.of());
            var plan = new RetrievalRoutePlan(null, null, null, null,
                    new AssemblyConfig(false, false, 10, 10, 2, List.of()), null);

            var candidate = new RetrievalCandidate("u1", 0.85, "bm25",
                    Map.of("text", "SMF配置相关内容"), null);

            when(repo.resolveSegmentsByIds(any(), any())).thenReturn(List.of());
            when(repo.getRelationsForSegments(any(), any(), any())).thenReturn(List.of());
            when(repo.getDocumentSources(any(), any())).thenReturn(List.of());

            var pack = assembler.assemble("SMF配置", understanding, scope, List.of(candidate), plan);
            assertThat(pack.items()).isNotEmpty();
            assertThat(pack.items().get(0).role()).isEqualTo("seed");
            assertThat(pack.items().get(0).score()).isEqualTo(0.85);
            assertThat(pack.issues()).isEmpty();
        }

        @Test
        @DisplayName("contextQuery populated from understanding")
        void contextQueryPopulated() {
            var understanding = new QueryUnderstanding("SMF配置", "concept_lookup",
                    null, null, null, null, EvidenceNeed.empty(), null, "rule", null);
            var scope = new ActiveScope("rel", "build", List.of("snap1"), Map.of());
            var plan = new RetrievalRoutePlan(null, null, null, null,
                    new AssemblyConfig(false, false, 10, 10, 2, List.of()), null);

            var candidate = new RetrievalCandidate("u1", 0.85, "bm25", Map.of(), null);

            when(repo.resolveSegmentsByIds(any(), any())).thenReturn(List.of());
            when(repo.getRelationsForSegments(any(), any(), any())).thenReturn(List.of());
            when(repo.getDocumentSources(any(), any())).thenReturn(List.of());

            var pack = assembler.assemble("SMF配置", understanding, scope, List.of(candidate), plan);
            assertThat(pack.query()).isNotNull();
            assertThat(pack.query().original()).isEqualTo("SMF配置");
            assertThat(pack.query().intent()).isEqualTo("concept_lookup");
        }
    }

    @Nested
    @DisplayName("seed provenance")
    class SeedProvenance {

        private static SegmentWithMetaRow segRow(String id, String documentId) {
            var row = new SegmentWithMetaRow();
            row.setId(id);
            row.setDocumentId(documentId);
            row.setDocumentSnapshotId("snap1");
            row.setRawText("段落原文");
            return row;
        }

        private static RetrievalCandidate candidateOn(String... segmentIds) {
            String ids = String.join("\",\"", segmentIds);
            return new RetrievalCandidate("u1", 0.85, "bm25",
                    Map.of("text", "命中内容",
                            "source_refs_json", "{\"raw_segment_ids\":[\"" + ids + "\"]}"),
                    null);
        }

        private static RetrievalRoutePlan noExpansionPlan() {
            return new RetrievalRoutePlan(null, null, null, null,
                    new AssemblyConfig(false, false, 10, 10, 2, List.of()), null);
        }

        private static QueryUnderstanding understanding() {
            return new QueryUnderstanding("SMF配置", "concept_lookup", null, null, null, null,
                    EvidenceNeed.empty(), null, "rule", null);
        }

        @Test
        @DisplayName("the seed that actually matched carries its document id")
        void seedCarriesSourceId() {
            var scope = new ActiveScope("rel", "build", List.of("snap1"), Map.of());

            when(repo.resolveSegmentsByIds(any(), any()))
                    .thenReturn(List.of(segRow("seg-1", "doc-7")));
            when(repo.getRelationsForSegments(any(), any(), any())).thenReturn(List.of());
            when(repo.getDocumentSources(any(), any())).thenReturn(List.of());

            var pack = assembler.assemble("SMF配置", understanding(), scope,
                    List.of(candidateOn("seg-1")), noExpansionPlan());

            var seed = pack.items().stream()
                    .filter(i -> "seed".equals(i.role())).findFirst().orElseThrow();
            assertThat(seed.sourceId()).isEqualTo("doc-7");
        }

        @Test
        @DisplayName("a seed spanning two documents stays unattributed rather than picking one")
        void ambiguousSeedIsNotGuessed() {
            var scope = new ActiveScope("rel", "build", List.of("snap1"), Map.of());

            // Content-deduplicated snapshots make this real: one segment, two owning documents.
            when(repo.resolveSegmentsByIds(any(), any()))
                    .thenReturn(List.of(segRow("seg-1", "doc-7"), segRow("seg-1", "doc-8")));
            when(repo.getRelationsForSegments(any(), any(), any())).thenReturn(List.of());
            when(repo.getDocumentSources(any(), any())).thenReturn(List.of());

            var pack = assembler.assemble("SMF配置", understanding(), scope,
                    List.of(candidateOn("seg-1")), noExpansionPlan());

            var seed = pack.items().stream()
                    .filter(i -> "seed".equals(i.role())).findFirst().orElseThrow();
            assertThat(seed.sourceId()).isNull();
        }

        @Test
        @DisplayName("sources carry kbId so a caller can tell which knowledge base answered")
        void sourcesCarryKbId() {
            var scope = new ActiveScope("rel", "build", List.of("snap1"), Map.of());

            var doc = new com.coremasterkb.serving.mapper.result.DocumentSourceRow();
            doc.setId("doc-7");
            doc.setDocumentKey("doc:/spec.pdf");
            doc.setKbId("kb-a");

            when(repo.resolveSegmentsByIds(any(), any()))
                    .thenReturn(List.of(segRow("seg-1", "doc-7")));
            when(repo.getRelationsForSegments(any(), any(), any())).thenReturn(List.of());
            when(repo.getDocumentSources(any(), any())).thenReturn(List.of(doc));

            var pack = assembler.assemble("SMF配置", understanding(), scope,
                    List.of(candidateOn("seg-1")), noExpansionPlan());

            assertThat(pack.sources()).hasSize(1);
            assertThat(pack.sources().get(0).kbId()).isEqualTo("kb-a");
        }

        @Test
        @DisplayName("a legacy document belongs to no KB and reports kbId null")
        void legacyDocumentHasNullKbId() {
            var scope = new ActiveScope("rel", "build", List.of("snap1"), Map.of());

            var doc = new com.coremasterkb.serving.mapper.result.DocumentSourceRow();
            doc.setId("doc-legacy");
            doc.setDocumentKey("doc:/legacy.md");
            // kb_id stays null: ingested via /api/runs, never uploaded into a knowledge base.

            when(repo.resolveSegmentsByIds(any(), any()))
                    .thenReturn(List.of(segRow("seg-1", "doc-legacy")));
            when(repo.getRelationsForSegments(any(), any(), any())).thenReturn(List.of());
            when(repo.getDocumentSources(any(), any())).thenReturn(List.of(doc));

            var pack = assembler.assemble("SMF配置", understanding(), scope,
                    List.of(candidateOn("seg-1")), noExpansionPlan());

            assertThat(pack.sources().get(0).kbId()).isNull();
        }
    }

    @Nested
    @DisplayName("item deduplication")
    class ItemDeduplication {

        /**
         * selectWithMeta LEFT JOINs asset_document_snapshot_links (1:N), so a segment whose
         * snapshot has multiple links comes back as several rows sharing the same id. Those
         * duplicate rows must not become duplicate context items.
         */
        @Test
        @DisplayName("fan-out rows for one segment id yield a single context item")
        void joinFanOutIsDeduped() {
            var understanding = new QueryUnderstanding("业务感知", "concept_lookup", null, null, null, null,
                    EvidenceNeed.empty(), null, "rule", null);
            var scope = new ActiveScope("rel", "build", List.of("snap1"), Map.of());
            var plan = new RetrievalRoutePlan(null, null, null, null,
                    new AssemblyConfig(false, false, 10, 10, 2, List.of()), null);

            // Candidate whose underlying source is seg1.
            var candidate = new RetrievalCandidate("u1", 0.85, "bm25",
                    Map.of("source_segment_id", "seg1", "text", "seed text"), null);

            // Same seg1 returned 3× (one row per snapshot link path) — the JOIN fan-out.
            when(repo.resolveSegmentsByIds(any(), any())).thenReturn(List.of(
                    seg("seg1", "业务感知定义_1.md"),
                    seg("seg1", "业务感知功能描述/业务感知定义_1.md"),
                    seg("seg1", "另一目录/业务感知定义_1.md")));
            when(repo.getRelationsForSegments(any(), any(), any())).thenReturn(List.of());
            when(repo.getDocumentSources(any(), any())).thenReturn(List.of());

            var pack = assembler.assemble("业务感知", understanding, scope, List.of(candidate), plan);

            long seg1Count = pack.items().stream()
                    .filter(i -> "seg1".equals(i.id()))
                    .count();
            assertThat(seg1Count).isEqualTo(1);
            assertThat(pack.items().stream().map(ContextItem::id).distinct().count())
                    .isEqualTo(pack.items().size());
        }

        /**
         * A segment reached both as a direct source (role=context) and via graph expansion
         * (role=support) must appear once; the first occurrence (context) wins.
         */
        @Test
        @DisplayName("segment reached via both source and expansion is deduped, context wins")
        void sourceAndExpansionOverlapIsDeduped() {
            var understanding = new QueryUnderstanding("业务感知", "concept_lookup", null, null, null, null,
                    EvidenceNeed.empty(), null, "rule", null);
            var scope = new ActiveScope("rel", "build", List.of("snap1"), Map.of());
            var plan = new RetrievalRoutePlan(null, null, null, null,
                    new AssemblyConfig(true, true, 10, 10, 2, List.of("elaborates")), null);

            var candidate = new RetrievalCandidate("u1", 0.85, "bm25",
                    Map.of("source_segment_id", "seg1", "text", "seed text"), null);

            when(repo.resolveSegmentsByIds(any(), any()))
                    .thenReturn(List.of(seg("seg1", "业务感知定义_1.md")));
            // Expansion returns the very same seg1 (cross-list duplicate).
            when(graphExpander.expand(any(), anyInt(), any(), anyInt(), any()))
                    .thenReturn(List.of(new ExpandedSegmentRow(
                            seg("seg1", "业务感知定义_1.md"), 1, "seg1", "elaborates")));
            when(repo.getRelationsForSegments(any(), any(), any())).thenReturn(List.of());
            when(repo.getDocumentSources(any(), any())).thenReturn(List.of());

            var pack = assembler.assemble("业务感知", understanding, scope, List.of(candidate), plan);

            var seg1Items = pack.items().stream()
                    .filter(i -> "seg1".equals(i.id()))
                    .toList();
            assertThat(seg1Items).hasSize(1);
            assertThat(seg1Items.get(0).role()).isEqualTo("context");
        }

        private SegmentWithMetaRow seg(String id, String relativePath) {
            var row = new SegmentWithMetaRow();
            row.setId(id);
            row.setDocumentSnapshotId("snap1");
            row.setRawText("业务感知是指对用户数据报文进行解析。");
            row.setBlockType("paragraph");
            row.setSemanticRole("definition");
            row.setSnapshotTitle("业务感知定义");
            row.setDocumentId("doc1");
            row.setRelativePath(relativePath);
            return row;
        }
    }
}
