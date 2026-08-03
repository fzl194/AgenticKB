package com.coremasterkb.serving.application;

import com.coremasterkb.serving.domain.*;
import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.domainpack.DatabaseConfig;
import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.domainpack.DomainPackReader;
import com.coremasterkb.serving.domainpack.DomainPoolManager;
import com.coremasterkb.serving.domainpack.DomainRegistry;
import com.coremasterkb.serving.domainpack.DomainRegistryEntry;
import com.coremasterkb.serving.domainpack.ServingDomainProfile;
import com.coremasterkb.serving.infrastructure.EmbeddingClient;
import com.coremasterkb.serving.infrastructure.LlmClient;
import com.coremasterkb.serving.mapper.AssetRetrievalUnitMapper;
import com.coremasterkb.serving.mapper.result.FtsResultRow;
import com.coremasterkb.serving.observability.SearchMetrics;
import com.coremasterkb.serving.observability.TraceCollector;
import com.coremasterkb.serving.pipeline.*;
import com.coremasterkb.serving.rerank.RerankPipeline;
import com.coremasterkb.serving.repository.AssetRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;
import java.util.concurrent.Executors;

/**
 * Main search orchestrator wiring all components together.
 *
 * <p>Pipeline: understand &rarr; route &rarr; resolve scope &rarr;
 * retrieve &rarr; fuse &rarr; rerank &rarr; assemble.
 * Each stage is traced via {@link TraceCollector}.
 * Matches Python's search() endpoint behavior.
 */
@Service
public class SearchService {

    private static final Logger log = LoggerFactory.getLogger(SearchService.class);

    private final QueryUnderstandingEngine quEngine;
    private final RetrievalRouter router;
    private final RetrievalOrchestrator orchestrator;
    private final RerankPipeline rerankPipeline;
    private final ContextAssembler assembler;
    private final DomainPackReader domainPackReader;
    private final DomainRegistry domainRegistry;
    private final DomainPoolManager domainPoolManager;
    private final EmbeddingClient embeddingClient;
    private final AssetRepository assetRepository;
    private final LlmClient llmClient;
    private final MultiQueryExpander multiQueryExpander;
    private final SemanticCacheService semanticCache;
    private final SearchMetrics metrics;
    private final TreeNavigator treeNavigator;
    private final AssetRetrievalUnitMapper retrievalUnitMapper;
    private final String defaultDomain;
    // Virtual-thread-per-task executor: no pool state to manage, no shutdown required.
    // Each CompletableFuture.supplyAsync spawns a new virtual thread that is cleaned up by the JVM.
    private final Executor pipelineExecutor = Executors.newVirtualThreadPerTaskExecutor();

    public SearchService(
            QueryUnderstandingEngine quEngine,
            RetrievalRouter router,
            RetrievalOrchestrator orchestrator,
            RerankPipeline rerankPipeline,
            ContextAssembler assembler,
            DomainPackReader domainPackReader,
            DomainRegistry domainRegistry,
            DomainPoolManager domainPoolManager,
            EmbeddingClient embeddingClient,
            AssetRepository assetRepository,
            LlmClient llmClient,
            MultiQueryExpander multiQueryExpander,
            SemanticCacheService semanticCache,
            SearchMetrics metrics,
            TreeNavigator treeNavigator,
            AssetRetrievalUnitMapper retrievalUnitMapper,
            ServingProperties properties) {
        this.quEngine = quEngine;
        this.router = router;
        this.orchestrator = orchestrator;
        this.rerankPipeline = rerankPipeline;
        this.assembler = assembler;
        this.domainPackReader = domainPackReader;
        this.domainRegistry = domainRegistry;
        this.domainPoolManager = domainPoolManager;
        this.embeddingClient = embeddingClient;
        this.assetRepository = assetRepository;
        this.llmClient = llmClient;
        this.multiQueryExpander = multiQueryExpander;
        this.semanticCache = semanticCache;
        this.metrics = metrics;
        this.treeNavigator = treeNavigator;
        this.retrievalUnitMapper = retrievalUnitMapper;
        this.defaultDomain = properties.defaultDomain();
        if (!embeddingClient.isConfigured()) {
            log.info("Embedding client not configured (LLM_SERVICE_URL blank) — dense retrieval disabled");
        }
    }

    /**
     * Execute the full search pipeline.
     *
     * @param request search request containing query, domain, scope, etc.
     * @return assembled context pack with results
     */
    public ContextPack search(SearchRequest request) {
        if (request.query() == null || request.query().isBlank()) {
            throw new IllegalArgumentException("query_required");
        }

        TraceCollector trace = new TraceCollector();

        // 1. Resolve domain first so all downstream components see it
        String effectiveDomain = (request.domain() != null && !request.domain().isBlank())
                ? request.domain() : defaultDomain;
        String channel = (request.channel() != null && !request.channel().isBlank())
                ? request.channel()
                : domainRegistry.getDefaultChannel(effectiveDomain);

        // Validate DB reachable before touching the routing DataSource
        // (throws domain_database_unavailable if the pool cannot connect)
        domainPoolManager.getDataSource(effectiveDomain);

        // All DB operations on this thread now route to the domain's pool
        DomainContext.set(effectiveDomain);
        // Propagate domain to LLM client BEFORE any parallel calls
        llmClient.setKnowledgeDomain(effectiveDomain);

        // Variables declared before try so they're accessible in debug section after finally
        QueryUnderstanding understanding = null;
        RetrievalRoutePlan routePlan = null;
        ActiveScope scope = null;
        List<RetrievalCandidate> ranked = List.of();
        ContextPack pack = null;
        float[] queryEmbedding = null;
        List<RouteTrace> debugRouteTraces = List.of();
        Set<String> navigatedSections = Set.of();

        try {

        // 2. Load Domain Profile
        ServingDomainProfile profile = domainPackReader.getProfile(effectiveDomain);

        // 3. Query Understanding + Embedding in parallel
        //    Embedding is started optimistically — result is only used if dense route is enabled.
        //    NOTE: Virtual threads cannot inherit ThreadLocal from the parent thread.
        //    We must explicitly set knowledge_domain in each async lambda.
        final String threadDomain = effectiveDomain;
        trace.startStage("query_understanding");
        CompletableFuture<QueryUnderstanding> understandingFuture = CompletableFuture.supplyAsync(
                () -> {
                    llmClient.setKnowledgeDomain(threadDomain);
                    try {
                        return quEngine.understand(request.query(), profile);
                    } finally {
                        llmClient.clearKnowledgeDomain();
                    }
                }, pipelineExecutor);
        CompletableFuture<float[]> embeddingFuture = embeddingClient.isConfigured()
                ? CompletableFuture.supplyAsync(() -> {
            llmClient.setKnowledgeDomain(threadDomain);
            try {
                return embeddingClient.embedHyDE(request.query());
            } catch (Exception e) {
                log.warn("Query embedding failed: {}", e.getMessage());
                return null;
            } finally {
                llmClient.clearKnowledgeDomain();
            }
        }, pipelineExecutor)
                : CompletableFuture.completedFuture(null);
        // Start multi-query expansion in parallel with QU and HyDE
        CompletableFuture<List<String>> variantsFuture = CompletableFuture.supplyAsync(() -> {
            llmClient.setKnowledgeDomain(threadDomain);
            try {
                return multiQueryExpander.expand(request.query());
            } catch (Exception e) {
                log.warn("Multi-query expansion failed, using original only: {}", e.getMessage());
                return List.of(request.query());
            } finally {
                llmClient.clearKnowledgeDomain();
            }
        }, pipelineExecutor);

        understanding = understandingFuture.join();
        trace.endStage("query_understanding",
                "intent=" + understanding.intent()
                        + ", entities=" + understanding.entities().size()
                        + ", source=" + understanding.source());
        log.info("[QU] intent={}, complexity={}, entities={}, source={}",
                understanding.intent(), understanding.queryComplexity(),
                understanding.entities().size(), understanding.source());
        metrics.recordIntent(understanding.intent());

        // 4. Retrieval Router
        trace.startStage("retrieval_router");
        routePlan = router.route(understanding, profile);
        trace.endStage("retrieval_router",
                "routes=" + routePlan.routes().size()
                        + ", fusion=" + routePlan.fusion().method());

        String dbLabel = domainRegistry.findEntry(effectiveDomain)
                .map(DomainRegistryEntry::database)
                .filter(DatabaseConfig::isUsable)
                .map(SearchService::describeDatabase)
                .orElse("default(shared)");
        log.info("[search] routing domain={} channel={} db={}", effectiveDomain, channel, dbLabel);

        trace.startStage("resolve_scope");
        try {
            scope = resolveActiveScope(effectiveDomain, channel);
        } catch (IllegalArgumentException e) {
            trace.endStage("resolve_scope", "error=" + e.getMessage());
            throw e;
        }
        trace.endStage("resolve_scope", "snapshots=" + scope.snapshotIds().size());

        // 3.4. Tree navigation: infer relevant chapters for query entities
        trace.startStage("tree_navigation");
        TreeNavigation nav = treeNavigator.inferSections(understanding.entities(), scope.snapshotIds());
        if (nav == null) nav = TreeNavigation.empty();
        navigatedSections = nav.softSections();
        List<String> sectionHardFilter = nav.hardFilter() ? nav.hardPrefixes() : List.of();
        trace.endStage("tree_navigation",
                "soft=" + navigatedSections.size() + ", hard=" + sectionHardFilter.size());

        // 3.5. Multi-Query Expansion (started in parallel with QU+HyDE)
        trace.startStage("multi_query_expand");
        List<String> queryVariants = variantsFuture.join();
        trace.endStage("multi_query_expand", "variants=" + queryVariants.size());

        // 5. Collect pre-computed embedding (started in parallel with understanding)
        boolean denseEnabled = routePlan.routes().stream()
                .anyMatch(r -> "dense_vector".equals(r.name()) && r.enabled());
        if (denseEnabled && embeddingClient.isConfigured()) {
            trace.startStage("embedding");
            queryEmbedding = embeddingFuture.join();
            trace.endStage("embedding",
                    "dim=" + (queryEmbedding != null ? queryEmbedding.length : 0));
        } else {
            queryEmbedding = embeddingFuture.join();
        }

        // 5.5. Semantic cache lookup
        trace.startStage("semantic_cache");
        ContextPack cachedPack = semanticCache.lookup(effectiveDomain, scope.releaseId(), queryEmbedding);
        if (cachedPack != null) {
            trace.endStage("semantic_cache", "hit=true");
            return cachedPack;
        }
        trace.endStage("semantic_cache", "hit=false");

        // 6. Pre-compute variant embeddings in batch (single API call instead of N)
        final float[] finalQueryEmbedding = queryEmbedding;
        Map<String, float[]> variantEmbeddingMap = new LinkedHashMap<>();
        if (denseEnabled && embeddingClient.isConfigured()) {
            List<String> nonOriginalVariants = queryVariants.stream()
                    .filter(v -> !v.equals(request.query()))
                    .toList();
            if (!nonOriginalVariants.isEmpty()) {
                try {
                    List<float[]> batchResults = embeddingClient.embedBatch(nonOriginalVariants);
                    for (int i = 0; i < nonOriginalVariants.size(); i++) {
                        float[] emb = i < batchResults.size() ? batchResults.get(i) : null;
                        if (emb != null) {
                            variantEmbeddingMap.put(nonOriginalVariants.get(i), emb);
                        }
                    }
                    log.info("[BatchEmbed] embedded {} variants in batch", nonOriginalVariants.size());
                } catch (Exception e) {
                    log.warn("Batch variant embedding failed, will use HyDE embedding: {}", e.getMessage());
                }
            }
        }

        // 6a. Retrieve for each variant (parallel via CompletableFuture)
        trace.startStage("retrieve");
        List<RetrievalCandidate> rawCandidates = Collections.synchronizedList(new ArrayList<>());
        List<RouteTrace> allRouteTraces = Collections.synchronizedList(new ArrayList<>());

        // Capture effectively-final copies for lambda use
        final QueryUnderstanding finalUnderstanding = understanding;
        final RetrievalRoutePlan finalRoutePlan = routePlan;
        final ActiveScope finalScope = scope;
        final List<String> finalSectionHardFilter = sectionHardFilter;

        List<CompletableFuture<Void>> variantFutures = new ArrayList<>();

        for (String variant : queryVariants) {
            // wrapRunnable, not a bare lambda: pipelineExecutor hands work to virtual threads, which
            // do NOT inherit ThreadLocals. Without this the retrieval below runs with a null
            // DomainContext — DomainRoutingDataSource then silently falls back to the default
            // DataSource, and EntityGraphRouteRetriever (which has no other source of domain)
            // returns nothing at all. RetrievalOrchestrator already wraps its own route fan-out,
            // but it can only propagate what its caller had set.
            CompletableFuture<Void> f = CompletableFuture.runAsync(DomainContext.wrapRunnable(() -> {
                try {
                    long vt0 = System.nanoTime();
                    QueryUnderstanding varUnderstanding = variant.equals(request.query())
                            ? finalUnderstanding
                            : buildVariantUnderstanding(finalUnderstanding, variant);
                    // Use pre-computed batch embedding, fall back to query embedding
                    float[] variantEmb = variant.equals(request.query())
                            ? finalQueryEmbedding
                            : variantEmbeddingMap.getOrDefault(variant, finalQueryEmbedding);
                    long orch0 = System.nanoTime();
                    OrchestratorResult varResult = orchestrator.execute(
                            varUnderstanding, finalRoutePlan, variantEmb,
                            finalScope.snapshotIds(), finalSectionHardFilter);
                    long orchMs = (System.nanoTime() - orch0) / 1_000_000;
                    log.info("[VARIANT-DIAG] variant='{}' total={}ms orch={}ms candidates={}",
                            variant.length() > 30 ? variant.substring(0, 30) + "..." : variant,
                            (System.nanoTime() - vt0) / 1_000_000, orchMs, varResult.candidates().size());
                    rawCandidates.addAll(varResult.candidates());
                    allRouteTraces.addAll(varResult.routeTraces());
                } catch (Exception e) {
                    log.error("Variant retrieval failed for '{}': {}", variant, e.getMessage());
                }
            }), pipelineExecutor);
            variantFutures.add(f);
        }

        // 6b. Sub-query decomposition (cap at 4) — also parallel
        for (SubQuery subQuery : understanding.subQueries().stream().limit(4).toList()) {
            // Same virtual-thread caveat as the variant loop above.
            CompletableFuture<Void> f = CompletableFuture.runAsync(DomainContext.wrapRunnable(() -> {
                try {
                    QueryUnderstanding subUnderstanding = buildSubQueryUnderstanding(finalUnderstanding, subQuery);
                    OrchestratorResult subResult = orchestrator.execute(
                            subUnderstanding, finalRoutePlan, finalQueryEmbedding,
                            finalScope.snapshotIds(), finalSectionHardFilter);
                    rawCandidates.addAll(subResult.candidates());
                    allRouteTraces.addAll(subResult.routeTraces());
                } catch (Exception e) {
                    log.error("Sub-query retrieval failed for '{}': {}", subQuery.text(), e.getMessage());
                }
            }), pipelineExecutor);
            variantFutures.add(f);
        }

        // Wait for all variant + sub-query retrievals to complete
        CompletableFuture.allOf(variantFutures.toArray(CompletableFuture[]::new)).join();

        trace.endStage("retrieve",
                "candidates=" + rawCandidates.size() + ", variants=" + queryVariants.size());

        debugRouteTraces = List.copyOf(allRouteTraces);

        // Observability: per-route candidate counts
        for (RouteTrace rt : allRouteTraces) {
            if (rt.attempted()) {
                metrics.recordRouteCandidates(rt.name(), rt.candidateCount());
            }
        }

        // 7. Fuse
        trace.startStage("fusion");
        FusionStrategy fusion = switch (routePlan.fusion().method()) {
            case "weighted_rrf" -> new WeightedRRFFusion();
            case "rrf" -> new RRFFusion();
            default -> new IdentityFusion();
        };
        List<RetrievalCandidate> fused = fusion.fuse(rawCandidates, routePlan);
        trace.endStage("fusion",
                "fused=" + fused.size() + ", method=" + routePlan.fusion().method());

        // 7.5. Hydrate: fetch text + JSON details for fused candidates BEFORE rerank.
        //      Retrieval SQL defers text/source_refs_json/target_ref_json (NULL aliases)
        //      to reduce per-route data transfer over high-RTT network.
        //      Hydration here fills them in once (post-fusion) so the model reranker,
        //      text-similarity dedup, and assembly all see full content.
        trace.startStage("hydrate");
        fused = hydrateCandidates(fused);
        trace.endStage("hydrate", "hydrated=" + fused.size());

        // 8. Rerank (cascading: model -> LLM -> score)
        trace.startStage("rerank");
        long rerankStartNanos = System.nanoTime();
        var rerankResult = rerankPipeline.rerank(fused, routePlan, understanding);
        metrics.recordRerankDuration((System.nanoTime() - rerankStartNanos) / 1_000_000.0);
        ranked = rerankResult.candidates();
        metrics.recordRerankFallback(resolveWinningRerankMethod(rerankResult.traces()));
        trace.endStage("rerank", "ranked=" + ranked.size());

        // 9. Assemble ContextPack
        trace.startStage("assembly");
        pack = assembler.assemble(
                request.query(), understanding, scope, ranked, routePlan, navigatedSections);
        trace.endStage("assembly", "items=" + pack.items().size());

        // Observability: empty-scope no-result
        boolean noSeedResult = pack.items().stream().noneMatch(i -> "seed".equals(i.role()));
        if (noSeedResult && understanding.scope().isEmpty()) {
            metrics.recordScopeEmpty();
        }

        // 9.5. Store result in semantic cache (best-effort)
        semanticCache.store(effectiveDomain, scope.releaseId(), request.query(), queryEmbedding, pack);

        } finally {
            DomainContext.clear();
            llmClient.clearKnowledgeDomain();
        }

        // Unreachable if an exception was thrown inside try, so pack is guaranteed non-null here
        ContextPack result = pack;
        if (request.debug()) {
            Trace fullTrace = trace.buildTrace();
            Map<String, Object> debugInfo = new LinkedHashMap<>();
            debugInfo.put("understanding", understandingToMap(understanding));
            debugInfo.put("route_plan", routePlanToMap(routePlan));
            debugInfo.put("domain_context", domainContextToMap(effectiveDomain, channel, scope));
            debugInfo.put("trace", traceToMap(fullTrace));
            debugInfo.put("candidate_count", ranked.size());
            debugInfo.put("fusion_method", routePlan.fusion().method());
            debugInfo.put("query_embedding_dim", queryEmbedding != null ? queryEmbedding.length : 0);
            if (!debugRouteTraces.isEmpty()) {
                debugInfo.put("route_traces", routeTracesToList(debugRouteTraces));
            }

            result = new ContextPack(
                    pack.query(),
                    pack.items(),
                    pack.relations(),
                    pack.sources(),
                    pack.evidenceGroups(),
                    pack.issues(),
                    pack.suggestions(),
                    debugInfo
            );
        }

        log.info("[search] OK items={}", result.items().size());
        return result;
    }

    // =========================================================================
    // Hydration: fetch text + JSON for top-K candidates after rerank
    // =========================================================================

    /**
     * Hydrate candidates with text, source_refs_json, and target_ref_json.
     * These heavy columns were excluded from retrieval SQL to reduce per-route data transfer
     * (138KB → ~11KB per route query). Hydration runs once post-fusion so the model reranker
     * and text-similarity dedup have full content. Fetches ~50 IDs (fused) instead of ~219
     * (3 routes × 73 rows pre-fusion).
     */
    private List<RetrievalCandidate> hydrateCandidates(List<RetrievalCandidate> candidates) {
        if (candidates.isEmpty()) return candidates;

        List<String> ids = candidates.stream()
                .map(RetrievalCandidate::retrievalUnitId)
                .distinct()
                .toList();

        long t0 = System.nanoTime();
        List<FtsResultRow> details;
        try {
            details = retrievalUnitMapper.fetchDetailsByIds(ids);
        } catch (Exception e) {
            log.warn("[HYDRATE] fetchDetailsByIds failed ({} IDs), proceeding with unhydrated candidates: {}",
                    ids.size(), e.getMessage());
            return candidates;
        }
        long hydrateMs = (System.nanoTime() - t0) / 1_000_000;

        Map<String, FtsResultRow> detailMap = new LinkedHashMap<>();
        for (FtsResultRow row : details) {
            detailMap.put(row.getId(), row);
        }

        List<RetrievalCandidate> hydrated = new ArrayList<>();
        for (var c : candidates) {
            FtsResultRow detail = detailMap.get(c.retrievalUnitId());
            if (detail != null) {
                Map<String, Object> meta = new LinkedHashMap<>(c.metadata());
                if (detail.getText() != null) meta.put("text", detail.getText());
                if (detail.getSourceRefsJson() != null) meta.put("source_refs_json", detail.getSourceRefsJson());
                if (detail.getTargetRefJson() != null) meta.put("target_ref_json", detail.getTargetRefJson());
                c = new RetrievalCandidate(c.retrievalUnitId(), c.score(), c.source(), meta, c.scoreChain());
            }
            hydrated.add(c);
        }

        long totalChars = hydrated.stream()
                .mapToLong(c -> c.metadata().get("text") instanceof String s ? s.length() : 0)
                .sum();
        log.info("[HYDRATE] hydrated {} candidates in {}ms ({}KB text fetched for {} IDs)",
                hydrated.size(), hydrateMs, totalChars / 1024, ids.size());
        return hydrated;
    }

    // =========================================================================
    // Scope resolution
    // =========================================================================

    private ActiveScope resolveActiveScope(String domain, String channel) {
        return assetRepository.resolveActiveScope(domain, channel);
    }

    private static String resolveWinningRerankMethod(List<RerankTraceStep> traces) {
        if (traces != null) {
            for (int i = traces.size() - 1; i >= 0; i--) {
                if (traces.get(i).succeeded()) {
                    return traces.get(i).provider();
                }
            }
        }
        return "score";
    }

    // =========================================================================
    // Multi-query helpers
    // =========================================================================

    private static QueryUnderstanding buildVariantUnderstanding(
            QueryUnderstanding original, String variantQuery) {
        return new QueryUnderstanding(
                variantQuery, original.intent(), original.subQueries(),
                original.entities(), original.scope(), original.keywords(),
                original.evidenceNeed(), original.ambiguities(), original.source(),
                original.queryComplexity()
        );
    }

    private static QueryUnderstanding buildSubQueryUnderstanding(
            QueryUnderstanding parent, SubQuery subQuery) {
        String intent = !"general".equals(subQuery.intent())
                ? subQuery.intent() : parent.intent();
        List<EntityRef> entities = !subQuery.entities().isEmpty()
                ? subQuery.entities() : parent.entities();
        return new QueryUnderstanding(
                subQuery.text(), intent, List.of(), entities,
                parent.scope(), parent.keywords(), parent.evidenceNeed(),
                parent.ambiguities(), parent.source(), parent.queryComplexity()
        );
    }

    // =========================================================================
    // Debug info helpers
    // =========================================================================

    private static Map<String, Object> understandingToMap(QueryUnderstanding u) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("original_query", u.originalQuery());
        map.put("intent", u.intent());
        map.put("source", u.source());
        map.put("keywords", u.keywords());
        map.put("entities_count", u.entities().size());
        return map;
    }

    private static Map<String, Object> routePlanToMap(RetrievalRoutePlan p) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("routes_count", p.routes().size());
        map.put("fusion_method", p.fusion().method());
        map.put("rerank_method", p.rerank().method());
        return map;
    }

    /** Human-readable DB label for logs/debug — host:port/dbname only, never credentials. */
    private static String describeDatabase(DatabaseConfig db) {
        if (db.jdbcUrl() != null && !db.jdbcUrl().isBlank()) return db.jdbcUrl();
        return db.host() + ":" + (db.port() != null ? db.port() : 5432) + "/" + db.dbname();
    }

    private Map<String, Object> domainContextToMap(String domain, String channel, ActiveScope scope) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("domain", domain);
        map.put("channel", channel);
        domainRegistry.findEntry(domain).ifPresentOrElse(
                entry -> map.put("database",
                        entry.database() != null && entry.database().isUsable()
                                ? describeDatabase(entry.database())
                                : "default(shared)"),
                () -> map.put("database", "n/a")
        );
        map.put("release_id", scope.releaseId());
        map.put("build_id", scope.buildId());
        map.put("snapshot_count", scope.snapshotIds().size());
        return map;
    }

    private static Map<String, Object> traceToMap(Trace t) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("request_id", t.requestId());
        map.put("total_duration_ms", t.totalDurationMs());
        List<Map<String, Object>> stages = new ArrayList<>();
        for (var s : t.stages()) {
            Map<String, Object> stageMap = new LinkedHashMap<>();
            stageMap.put("name", s.name());
            stageMap.put("duration_ms", s.durationMs());
            stageMap.put("summary", s.outputSummary());
            stages.add(stageMap);
        }
        map.put("stages", stages);
        return map;
    }

    private static List<Map<String, Object>> routeTracesToList(List<RouteTrace> traces) {
        List<Map<String, Object>> list = new ArrayList<>();
        for (var t : traces) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("route", t.name());
            m.put("attempted", t.attempted());
            m.put("candidate_count", t.candidateCount());
            m.put("skipped_reason", t.skippedReason());
            m.put("latency_ms", Math.round(t.latencyMs() * 100.0) / 100.0);
            list.add(m);
        }
        return list;
    }
}
