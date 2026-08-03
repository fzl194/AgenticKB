package com.coremasterkb.serving.application;

import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.domain.*;
import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.domainpack.DomainPackReader;
import com.coremasterkb.serving.domainpack.DomainPoolManager;
import com.coremasterkb.serving.domainpack.DomainRegistry;
import com.coremasterkb.serving.mapper.AssetRetrievalUnitMapper;
import com.coremasterkb.serving.mapper.result.FtsResultRow;
import com.coremasterkb.serving.observability.SearchMetrics;
import com.coremasterkb.serving.infrastructure.EmbeddingClient;
import com.coremasterkb.serving.infrastructure.LlmClient;
import com.coremasterkb.serving.pipeline.*;
import com.coremasterkb.serving.rerank.RerankPipeline;
import com.coremasterkb.serving.rerank.RerankPipeline.RerankResult;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@DisplayName("SearchService")
class SearchServiceTest {

    private QueryUnderstandingEngine quEngine;
    private RetrievalRouter router;
    private RetrievalOrchestrator orchestrator;
    private RerankPipeline rerankPipeline;
    private ContextAssembler assembler;
    private DomainPackReader domainPackReader;
    private DomainRegistry domainRegistry;
    private DomainPoolManager domainPoolManager;
    private EmbeddingClient embeddingClient;
    private com.coremasterkb.serving.repository.AssetRepository assetRepo;
    private AssetRetrievalUnitMapper retrievalUnitMapper;
    private SearchService searchService;

    @BeforeEach
    void setUp() {
        quEngine = mock(QueryUnderstandingEngine.class);
        router = mock(RetrievalRouter.class);
        orchestrator = mock(RetrievalOrchestrator.class);
        rerankPipeline = mock(RerankPipeline.class);
        assembler = mock(ContextAssembler.class);
        domainPackReader = mock(DomainPackReader.class);
        domainRegistry = mock(DomainRegistry.class);
        domainPoolManager = mock(DomainPoolManager.class);
        embeddingClient = mock(EmbeddingClient.class);
        assetRepo = mock(com.coremasterkb.serving.repository.AssetRepository.class);
        retrievalUnitMapper = mock(AssetRetrievalUnitMapper.class);

        when(domainRegistry.getDefaultChannel(anyString())).thenReturn("prod");
        when(domainRegistry.findEntry(anyString())).thenReturn(java.util.Optional.empty());
        when(domainPoolManager.getDataSource(anyString())).thenReturn(mock(javax.sql.DataSource.class));
        when(assetRepo.resolveActiveScope(anyString(), anyString(), any()))
                .thenReturn(new ActiveScope("rel1", "b1", List.of("snap1"), Map.of()));

        // No kbIds in these requests, so authorize() is the identity on an empty list.
        var kbAccessService = mock(KbAccessService.class);
        when(kbAccessService.authorize(anyString(), any(), any())).thenReturn(List.of());

        var multiQueryExpander = mock(MultiQueryExpander.class);
        when(multiQueryExpander.expand(anyString())).thenReturn(List.of("SMF配置"));
        var semanticCache = mock(SemanticCacheService.class);
        when(semanticCache.lookup(anyString(), anyString(), any())).thenReturn(null);
        var metrics = mock(SearchMetrics.class);
        var treeNavigator = mock(TreeNavigator.class);
        when(treeNavigator.inferSections(any(), any())).thenReturn(TreeNavigation.empty());

        searchService = new SearchService(
                quEngine, router, orchestrator, rerankPipeline,
                assembler, domainPackReader, domainRegistry, domainPoolManager,
                embeddingClient, assetRepo,
                mock(LlmClient.class),
                multiQueryExpander, semanticCache, metrics, treeNavigator,
                retrievalUnitMapper, kbAccessService,
                new ServingProperties(null, null, "cloud_core_network", null, null));
    }

    @Nested
    @DisplayName("full pipeline orchestration")
    class FullPipeline {
        @Test
        @DisplayName("all pipeline stages called in order")
        void allStagesCalled() {
            var understanding = new QueryUnderstanding("SMF配置", "concept_lookup",
                    List.of(), List.of(), Map.of(), List.of("SMF"),
                    EvidenceNeed.empty(), List.of(), "rule", null);
            var routePlan = new RetrievalRoutePlan(
                    List.of(new RouteConfig("lexical_bm25", true, 1.0, 50)),
                    Map.of(), new FusionConfig("identity", 60),
                    new RerankConfig("score", "score"),
                    AssemblyConfig.defaults(), ExpansionConfig.defaults());
            var orchResult = OrchestratorResult.empty();
            var rerankResult = new RerankResult(List.of(), List.of());
            var expectedPack = new ContextPack(null, List.of(), List.of(), List.of(),
                    List.of(), List.of(), List.of(), Map.of());

            when(domainPackReader.getProfile(anyString())).thenReturn(null);
            when(quEngine.understand(anyString(), any())).thenReturn(understanding);
            when(router.route(any(), any())).thenReturn(routePlan);
            when(orchestrator.execute(any(), any(), any(), anyList(), anyList())).thenReturn(orchResult);
            when(rerankPipeline.rerank(any(), any(), any())).thenReturn(rerankResult);
            when(assembler.assemble(anyString(), any(), any(), any(), any(), any())).thenReturn(expectedPack);

            var request = new SearchRequest("SMF配置", Map.of(), List.of(), false,
                    "cloud_core_network", null, "evidence");

            var result = searchService.search(request);

            assertThat(result).isNotNull();
            verify(quEngine).understand("SMF配置", null);
            verify(router).route(understanding, null);
            verify(orchestrator).execute(eq(understanding), eq(routePlan), any(), eq(List.of("snap1")), anyList());
            verify(rerankPipeline).rerank(any(), eq(routePlan), eq(understanding));
            verify(assembler).assemble(eq("SMF配置"), eq(understanding), any(), any(), eq(routePlan), any());
        }

        @Test
        @DisplayName("debug flag populates debug map in result")
        void debugFlagPopulatesDebugMap() {
            var understanding = new QueryUnderstanding("test", "general",
                    List.of(), List.of(), Map.of(), List.of(),
                    EvidenceNeed.empty(), List.of(), "rule", null);
            var routePlan = new RetrievalRoutePlan(
                    List.of(new RouteConfig("lexical_bm25", true, 1.0, 50)),
                    Map.of(), new FusionConfig("identity", 60),
                    new RerankConfig("score", "score"),
                    AssemblyConfig.defaults(), ExpansionConfig.defaults());

            when(domainPackReader.getProfile(anyString())).thenReturn(null);
            when(quEngine.understand(anyString(), any())).thenReturn(understanding);
            when(router.route(any(), any())).thenReturn(routePlan);
            when(orchestrator.execute(any(), any(), any(), anyList(), anyList())).thenReturn(OrchestratorResult.empty());
            when(rerankPipeline.rerank(any(), any(), any())).thenReturn(new RerankResult(List.of(), List.of()));
            when(assembler.assemble(anyString(), any(), any(), any(), any(), any()))
                    .thenReturn(new ContextPack(null, List.of(), List.of(), List.of(), List.of(), List.of(), List.of(), Map.of()));

            var request = new SearchRequest("test", Map.of(), List.of(), true,
                    "cloud_core_network", null, "evidence");
            var result = searchService.search(request);

            assertThat(result.debug()).containsKey("understanding");
            assertThat(result.debug()).containsKey("trace");
            assertThat(result.debug()).containsKey("fusion_method");
        }

        @Test
        @DisplayName("DomainContext is cleared even when pipeline throws")
        void domainContextClearedOnException() {
            var understanding = new QueryUnderstanding("test", "general",
                    List.of(), List.of(), Map.of(), List.of(),
                    EvidenceNeed.empty(), List.of(), "rule", null);
            var routePlan = new RetrievalRoutePlan(
                    List.of(new RouteConfig("lexical_bm25", true, 1.0, 50)),
                    Map.of(), new FusionConfig("identity", 60),
                    new RerankConfig("score", "score"),
                    AssemblyConfig.defaults(), ExpansionConfig.defaults());

            when(domainPackReader.getProfile(anyString())).thenReturn(null);
            when(quEngine.understand(anyString(), any())).thenReturn(understanding);
            when(router.route(any(), any())).thenReturn(routePlan);
            when(assetRepo.resolveActiveScope(anyString(), anyString(), any()))
                    .thenThrow(new IllegalArgumentException("no_active_release"));

            var request = new SearchRequest("test", Map.of(), List.of(), false,
                    "cloud_core_network", null, "evidence");

            assertThatThrownBy(() -> searchService.search(request))
                    .isInstanceOf(IllegalArgumentException.class);

            assertThat(DomainContext.get())
                    .as("DomainContext must be cleared after exception")
                    .isNull();
        }

        @Test
        @DisplayName("hydration runs before rerank and fills text metadata")
        void hydrateBeforeRerank() {
            var candidate = new RetrievalCandidate(
                    "ru-1", 0.9, "lexical_bm25",
                    Map.of("title", "SMF Config"),
                    new ScoreChain(0.9, 0.0, 0.0, List.of("lexical_bm25")));
            var orchResult = new OrchestratorResult(List.of(candidate), List.of());

            var detail = new FtsResultRow();
            detail.setId("ru-1");
            detail.setText("PFCP session parameters include heartbeat interval...");
            detail.setSourceRefsJson("{\"raw_segment_ids\":[\"seg-1\"]}");

            var understanding = new QueryUnderstanding("SMF配置", "concept_lookup",
                    List.of(), List.of(), Map.of(), List.of("SMF"),
                    EvidenceNeed.empty(), List.of(), "rule", null);
            var routePlan = new RetrievalRoutePlan(
                    List.of(new RouteConfig("lexical_bm25", true, 1.0, 50)),
                    Map.of(), new FusionConfig("identity", 60),
                    new RerankConfig("score", "score"),
                    AssemblyConfig.defaults(), ExpansionConfig.defaults());

            when(domainPackReader.getProfile(anyString())).thenReturn(null);
            when(quEngine.understand(anyString(), any())).thenReturn(understanding);
            when(router.route(any(), any())).thenReturn(routePlan);
            when(orchestrator.execute(any(), any(), any(), anyList(), anyList())).thenReturn(orchResult);
            when(retrievalUnitMapper.fetchDetailsByIds(anyList())).thenReturn(List.of(detail));

            var rerankResult = new RerankResult(List.of(candidate), List.of());
            when(rerankPipeline.rerank(any(), any(), any())).thenReturn(rerankResult);
            when(assembler.assemble(anyString(), any(), any(), any(), any(), any()))
                    .thenReturn(new ContextPack(null, List.of(), List.of(), List.of(),
                            List.of(), List.of(), List.of(), Map.of()));

            var request = new SearchRequest("SMF配置", Map.of(), List.of(), false,
                    "cloud_core_network", null, "evidence");
            searchService.search(request);

            // Verify hydration happened before rerank
            InOrder inOrder = inOrder(retrievalUnitMapper, rerankPipeline);
            inOrder.verify(retrievalUnitMapper).fetchDetailsByIds(anyList());
            inOrder.verify(rerankPipeline).rerank(any(), any(), any());

            // Verify the rerank input contains hydrated text
            ArgumentCaptor<List<RetrievalCandidate>> captor = ArgumentCaptor.forClass(List.class);
            verify(rerankPipeline).rerank(captor.capture(), any(), any());
            List<RetrievalCandidate> rerankInput = captor.getValue();
            assertThat(rerankInput).hasSize(1);
            assertThat(rerankInput.get(0).metadata())
                    .containsEntry("text", "PFCP session parameters include heartbeat interval...")
                    .containsEntry("source_refs_json", "{\"raw_segment_ids\":[\"seg-1\"]}");
        }

        @Test
        @DisplayName("hydration failure degrades gracefully — rerank still runs")
        void hydrationFailureDegradesGracefully() {
            var candidate = new RetrievalCandidate(
                    "ru-1", 0.9, "lexical_bm25",
                    Map.of("title", "SMF Config"),
                    new ScoreChain(0.9, 0.0, 0.0, List.of("lexical_bm25")));
            var orchResult = new OrchestratorResult(List.of(candidate), List.of());

            var understanding = new QueryUnderstanding("SMF配置", "concept_lookup",
                    List.of(), List.of(), Map.of(), List.of("SMF"),
                    EvidenceNeed.empty(), List.of(), "rule", null);
            var routePlan = new RetrievalRoutePlan(
                    List.of(new RouteConfig("lexical_bm25", true, 1.0, 50)),
                    Map.of(), new FusionConfig("identity", 60),
                    new RerankConfig("score", "score"),
                    AssemblyConfig.defaults(), ExpansionConfig.defaults());

            when(domainPackReader.getProfile(anyString())).thenReturn(null);
            when(quEngine.understand(anyString(), any())).thenReturn(understanding);
            when(router.route(any(), any())).thenReturn(routePlan);
            when(orchestrator.execute(any(), any(), any(), anyList(), anyList())).thenReturn(orchResult);
            when(retrievalUnitMapper.fetchDetailsByIds(anyList()))
                    .thenThrow(new RuntimeException("connection reset"));

            var rerankResult = new RerankResult(List.of(candidate), List.of());
            when(rerankPipeline.rerank(any(), any(), any())).thenReturn(rerankResult);
            when(assembler.assemble(anyString(), any(), any(), any(), any(), any()))
                    .thenReturn(new ContextPack(null, List.of(), List.of(), List.of(),
                            List.of(), List.of(), List.of(), Map.of()));

            var request = new SearchRequest("SMF配置", Map.of(), List.of(), false,
                    "cloud_core_network", null, "evidence");

            // Should NOT throw — request should succeed with unhydrated candidates
            var result = searchService.search(request);
            assertThat(result).isNotNull();

            // Rerank was still called with the original (unhydrated) candidate
            ArgumentCaptor<List<RetrievalCandidate>> captor = ArgumentCaptor.forClass(List.class);
            verify(rerankPipeline).rerank(captor.capture(), any(), any());
            List<RetrievalCandidate> rerankInput = captor.getValue();
            assertThat(rerankInput).hasSize(1);
            // Text should NOT be present (hydration failed)
            assertThat(rerankInput.get(0).metadata()).doesNotContainKey("text");
        }
    }
}
