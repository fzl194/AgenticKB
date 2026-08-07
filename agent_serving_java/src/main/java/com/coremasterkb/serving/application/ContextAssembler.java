package com.coremasterkb.serving.application;

import com.coremasterkb.serving.domain.*;
import com.coremasterkb.serving.evidence.EvidenceRoleClassifier;
import com.coremasterkb.serving.mapper.result.DocumentSourceRow;
import com.coremasterkb.serving.mapper.result.ExpandedSegmentRow;
import com.coremasterkb.serving.mapper.result.RelationRow;
import com.coremasterkb.serving.mapper.result.SegmentWithMetaRow;
import com.coremasterkb.serving.repository.AssetRepository;
import com.coremasterkb.serving.retrieval.GraphExpander;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.*;

/**
 * Builds ContextPack from retrieval results.
 *
 * <p>Pipeline: seed items &rarr; source drill-down &rarr; graph expansion &rarr;
 * deduplicate &rarr; build source refs &rarr; build issues &rarr; truncate &rarr;
 * build ContextQuery &rarr; pack into ContextPack.
 * Matches Python's ContextAssembler behavior.
 */
@Component
public class ContextAssembler {

    private static final Logger log = LoggerFactory.getLogger(ContextAssembler.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    // Context compression budget: 3000 tokens × ~4 chars/token
    private static final int MAX_TOTAL_TOKENS = 3000;
    private static final int MAX_TOTAL_CHARS = MAX_TOTAL_TOKENS * 4;

    // Issue type constants (match Python)
    private static final String ISSUE_NO_RESULT = "no_result";
    private static final String ISSUE_LOW_CONFIDENCE = "low_confidence";
    private static final String ISSUE_CONFLICTING_EVIDENCE = "conflicting_evidence";

    // Kind/role constants
    private static final String KIND_RETRIEVAL_UNIT = "retrieval_unit";
    private static final String KIND_RAW_SEGMENT = "raw_segment";
    private static final String ROLE_SEED = "seed";
    private static final String ROLE_CONTEXT = "context";
    private static final String ROLE_SUPPORT = "support";

    // RST discourse roles
    private static final String DISCOURSE_NUCLEUS = "nucleus";
    private static final String DISCOURSE_SATELLITE = "satellite";
    private static final String DISCOURSE_STANDALONE = "standalone";

    /**
     * Phase 2 – RST relation weights used as initial scores for expanded items.
     * Mirrors the priority order in GraphExpander: higher weight = higher score = more context budget.
     */
    private static final Map<String, Double> RST_RELATION_WEIGHTS = Map.ofEntries(
            Map.entry("elaborates",           1.5),
            Map.entry("conditions",           1.4),
            Map.entry("backgrounds",          1.3),
            Map.entry("enables",              1.2),
            Map.entry("results_in",           1.1),
            Map.entry("sequences",            1.0),
            Map.entry("contrasts_with",       0.9),
            Map.entry("causes",               0.8),
            Map.entry("parallels",            0.7),
            Map.entry("evidences",            1.35),
            Map.entry("exemplifies",          1.25),
            Map.entry("purposes",             1.15),
            Map.entry("justifies",            1.10),
            Map.entry("summarizes",           1.05),
            Map.entry("concedes",             0.85),
            Map.entry("section_header_of",    0.6),
            Map.entry("same_section",         0.5),
            Map.entry("previous",             0.4),
            Map.entry("next",                 0.4),
            Map.entry("same_parent_section",  0.3)
    );

    // RST relation type -> evidence role mapping for expansion
    private static final Map<String, String> RST_ROLE_MAP = Map.ofEntries(
            Map.entry("elaborates", "support"),
            Map.entry("conditions", "support"),
            Map.entry("causes", "support"),
            Map.entry("results_in", "support"),
            Map.entry("backgrounds", "background"),
            Map.entry("enables", "support"),
            Map.entry("evidences", "support"),
            Map.entry("exemplifies", "support"),
            Map.entry("purposes", "support"),
            Map.entry("justifies", "support"),
            Map.entry("summarizes", "support"),
            Map.entry("concedes", "contrast"),
            Map.entry("parallels", "context"),
            Map.entry("contrasts_with", "contrast"),
            Map.entry("previous", "context"),
            Map.entry("next", "context"),
            Map.entry("same_section", "context"),
            Map.entry("same_parent_section", "context"),
            Map.entry("section_header_of", "context")
    );

    private final AssetRepository repo;
    private final GraphExpander graphExpander;
    private final EvidenceRoleClassifier evidenceRoleClassifier = new EvidenceRoleClassifier();

    public ContextAssembler(AssetRepository repo, GraphExpander graphExpander) {
        this.repo = repo;
        this.graphExpander = graphExpander;
    }

    /**
     * Backward-compatible entry point: delegates with empty navigated sections.
     */
    public ContextPack assemble(
            String query,
            QueryUnderstanding understanding,
            ActiveScope scope,
            List<RetrievalCandidate> candidates,
            RetrievalRoutePlan routePlan) {
        return assemble(query, understanding, scope, candidates, routePlan, Set.of());
    }

    /**
     * Full assembly with tree-navigation hints: seeds whose source segment falls in a
     * navigated chapter ({@code navigatedSections} = lower-cased section_path prefixes)
     * are ranked ahead, then nucleus-first within each tier. An empty navigatedSections
     * applies no section bias (full-base behavior).
     */
    public ContextPack assemble(
            String query,
            QueryUnderstanding understanding,
            ActiveScope scope,
            List<RetrievalCandidate> candidates,
            RetrievalRoutePlan routePlan,
            Set<String> navigatedSections) {

        if (navigatedSections == null) navigatedSections = Set.of();
        if (candidates == null) candidates = List.of();
        if (scope == null) scope = new ActiveScope("", "", List.of(), Map.of());
        if (routePlan == null) routePlan = new RetrievalRoutePlan(List.of(), Map.of(), null, null, null, null);

        // 1. Resolve source segment IDs from candidates
        List<String> allSourceSegmentIds = new ArrayList<>();
        for (var candidate : candidates) {
            allSourceSegmentIds.addAll(resolveCandidateSources(candidate));
        }

        // Deduplicate segment IDs
        Set<String> seenSegs = new LinkedHashSet<>();
        List<String> uniqueSegIds = new ArrayList<>();
        for (String sid : allSourceSegmentIds) {
            if (seenSegs.add(sid)) {
                uniqueSegIds.add(sid);
            }
        }

        // 2. Fetch source segments
        List<SegmentWithMetaRow> sourceSegments;
        if (!uniqueSegIds.isEmpty() && scope.snapshotIds() != null && !scope.snapshotIds().isEmpty()) {
            sourceSegments = repo.resolveSegmentsByIds(uniqueSegIds, scope.snapshotIds());
        } else {
            sourceSegments = List.of();
        }
        Map<String, SegmentWithMetaRow> sourceSegMap = new LinkedHashMap<>();
        for (var seg : sourceSegments) {
            if (seg.getId() != null) {
                sourceSegMap.put(seg.getId(), seg);
            }
        }

        // Built from every row rather than the deduplicated map above: selectWithMeta joins the
        // 1:N snapshot-link table, so a segment whose snapshot backs several documents appears
        // once per document. Collapsing to a single row here would pick an arbitrary winner and
        // then present it as the seed's provenance.
        Map<String, Set<String>> segToDocuments = new HashMap<>();
        for (var seg : sourceSegments) {
            if (seg.getId() != null && seg.getDocumentId() != null && !seg.getDocumentId().isBlank()) {
                segToDocuments.computeIfAbsent(seg.getId(), k -> new LinkedHashSet<>())
                        .add(seg.getDocumentId());
            }
        }

        // Build section prefix map for tree-nav section bias
        Map<String, String> segDiscourseRole = buildDiscourseRoleMap(sourceSegments);
        Map<String, String> segSectionPrefix = buildSectionPrefixMap(sourceSegments);

        // 3. Build seed items (tree-nav section bias, then discourse nucleus-first)
        List<ContextItem> seedItems = buildSeedItems(
                candidates, understanding, segDiscourseRole, segSectionPrefix, navigatedSections,
                segToDocuments);
        List<ContextItem> sourceItems = buildSourceItems(sourceSegments);

        // 4. Graph expansion if enabled
        List<ContextItem> expandedItems = new ArrayList<>();
        List<ContextRelation> relationItems = new ArrayList<>();

        boolean expansionEnabled = routePlan.assembly() != null && routePlan.assembly().relationExpansion();
        if (expansionEnabled && !uniqueSegIds.isEmpty() && scope.snapshotIds() != null && !scope.snapshotIds().isEmpty()) {
            int maxDepth = routePlan.assembly().maxRelationDepth();
            int maxResults = routePlan.assembly().maxExpanded();
            List<String> relationTypes = routePlan.assembly().relationTypes();

            List<ExpandedSegmentRow> expansions = graphExpander.expand(
                    uniqueSegIds, maxDepth, relationTypes, maxResults, scope.snapshotIds());

            expandedItems = buildExpandedItems(expansions);

            // Build expansion relations using the actual root seed that triggered each expansion
            for (var exp : expansions) {
                SegmentWithMetaRow seg = exp.segment();
                if (seg != null && seg.getId() != null) {
                    String fromId = exp.sourceSegmentId() != null && !exp.sourceSegmentId().isBlank()
                            ? exp.sourceSegmentId()
                            : (uniqueSegIds.isEmpty() ? "" : uniqueSegIds.get(0));
                    relationItems.add(new ContextRelation(
                            "rel-" + fromId + "-" + seg.getId(),
                            fromId,
                            seg.getId(),
                            "expansion",
                            exp.expansionDistance()
                    ));
                }
            }
        }

        // 5. Fetch direct relations
        if (!uniqueSegIds.isEmpty()) {
            List<String> relTypes = routePlan.assembly() != null ? routePlan.assembly().relationTypes() : null;
            List<RelationRow> directRelations = repo.getRelationsForSegments(uniqueSegIds, relTypes,
                    scope.snapshotIds());
            for (var rel : directRelations) {
                String rid = rel.getId() != null ? rel.getId() : UUID.randomUUID().toString();
                relationItems.add(new ContextRelation(
                        rid,
                        rel.getFromSegmentId(),
                        rel.getToSegmentId(),
                        rel.getRelationType(),
                        0
                ));
            }
        }

        // Deduplicate relations
        Set<String> seenRels = new HashSet<>();
        List<ContextRelation> uniqueRelations = new ArrayList<>();
        for (var r : relationItems) {
            if (seenRels.add(r.id())) {
                uniqueRelations.add(r);
            }
        }

        // 6. Build source references
        Set<String> documentIds = new LinkedHashSet<>();
        for (var seg : sourceSegments) {
            if (seg.getDocumentId() != null && !seg.getDocumentId().isBlank()) {
                documentIds.add(seg.getDocumentId());
            }
        }
        List<DocumentSourceRow> docSources = !documentIds.isEmpty()
                ? repo.getDocumentSources(new ArrayList<>(documentIds), scope.snapshotIds())
                : List.of();
        List<SourceRef> sources = buildSources(docSources);

        // 7. Build issues
        List<Issue> issues = buildIssues(seedItems, understanding);

        // 8. Classify evidence roles for all items, then assemble and truncate
        seedItems = evidenceRoleClassifier.classify(seedItems, understanding);
        List<ContextItem> allItems = new ArrayList<>(seedItems);
        allItems.addAll(sourceItems);
        allItems.addAll(expandedItems);

        // Deduplicate items by id (keep first occurrence: seed > context > support).
        // Backstop for the JOIN fan-out and for segments reached via both source and expansion.
        Set<String> seenItemIds = new HashSet<>();
        List<ContextItem> dedupedItems = new ArrayList<>(allItems.size());
        for (var item : allItems) {
            if (item.id() == null || seenItemIds.add(item.id())) {
                dedupedItems.add(item);
            }
        }
        allItems = dedupedItems;

        AssemblyConfig assembly = routePlan.assembly() != null ? routePlan.assembly() : AssemblyConfig.defaults();
        int maxItems = assembly.maxItems() + assembly.maxExpanded();
        if (allItems.size() > maxItems) {
            allItems = allItems.subList(0, maxItems);
        }

        // Context compression — keep total text under MAX_TOTAL_CHARS
        List<String> ckw = understanding != null ? understanding.keywords() : List.of();
        String cq = understanding != null ? understanding.originalQuery() : query;
        allItems = compressItems(new ArrayList<>(allItems), ckw, cq);

        // Filter relations: only keep edges where both endpoints exist in final items
        Set<String> itemIds = new HashSet<>();
        for (var item : allItems) {
            itemIds.add(item.id());
        }
        List<ContextRelation> filteredRelations = new ArrayList<>();
        for (var r : uniqueRelations) {
            if (itemIds.contains(r.fromId()) && itemIds.contains(r.toId())) {
                filteredRelations.add(r);
            }
        }

        // 9. Build ContextQuery from understanding
        String releaseId = scope.releaseId() != null ? scope.releaseId() : "";
        String buildId   = scope.buildId()   != null ? scope.buildId()   : "";
        int snapshotCount = scope.snapshotIds() != null ? scope.snapshotIds().size() : 0;
        ContextQuery contextQuery;
        if (understanding != null) {
            contextQuery = new ContextQuery(
                    query,
                    formatUnderstanding(understanding),
                    understanding.intent(),
                    understanding.entities(),
                    understanding.scope(),
                    understanding.keywords(),
                    understanding.source(),
                    releaseId,
                    buildId,
                    snapshotCount
            );
        } else {
            contextQuery = new ContextQuery(query, "", null, null, null, null,
                    null, releaseId, buildId, snapshotCount);
        }

        // 10. Contradiction detection — flag conflicting semantic_role values in seeds
        //     and contrasting items from expansion (contrasts_with relations)
        detectContradictions(seedItems, issues);

        // Scan for contrasting expansion items
        List<ContextItem> contrastingItems = allItems.stream()
                .filter(item -> Boolean.TRUE.equals(item.metadata().get("is_contrasting")))
                .toList();
        if (!contrastingItems.isEmpty()) {
            issues.add(new Issue(
                    ISSUE_CONFLICTING_EVIDENCE,
                    "检测到矛盾信息：以下内容与主要检索结果存在对比关系，请注意辨别",
                    Map.of("conflicting_count", contrastingItems.size(),
                           "conflicting_ids", contrastingItems.stream().map(ContextItem::id).toList())
            ));
            log.info("[assemble] conflict detected: {} contrasting items", contrastingItems.size());
        }

        // 11. Build evidence groups and suggestions
        List<EvidenceGroup> evidenceGroups = buildEvidenceGroups(allItems, filteredRelations);
        List<String> suggestions = buildSuggestions(issues);

        return new ContextPack(
                contextQuery,
                allItems,
                filteredRelations,
                sources,
                evidenceGroups,
                issues,
                suggestions,
                Map.of()
        );
    }

    // =========================================================================
    // Seed item building
    // =========================================================================

    private List<ContextItem> buildSeedItems(
            List<RetrievalCandidate> candidates,
            QueryUnderstanding understanding,
            Map<String, String> segDiscourseRole,
            Map<String, String> segSectionPrefix,
            Set<String> navigatedSections,
            Map<String, Set<String>> segToDocuments) {
        List<ContextItem> items = new ArrayList<>();
        for (var c : candidates) {
            Map<String, Object> citation = buildCitation(c);

            List<String> routeSources = List.of();
            if (c.scoreChain() != null && c.scoreChain().routeSources() != null) {
                routeSources = c.scoreChain().routeSources();
            }

            // Attach the discourse role + navigated-chapter membership of the seed's
            // underlying segment(s) for classification and reordering.
            Map<String, Object> metadata = new LinkedHashMap<>(c.metadata());
            metadata.put("discourse_role", resolveSeedDiscourseRole(c, segDiscourseRole));
            String sectionPrefix = resolveSeedSectionPrefix(c, segSectionPrefix);
            metadata.put("section_path_prefix", sectionPrefix != null ? sectionPrefix : "");
            metadata.put("in_navigated_section",
                    sectionPrefix != null && navigatedSections.contains(sectionPrefix));

            items.add(new ContextItem(
                    c.retrievalUnitId(),
                    KIND_RETRIEVAL_UNIT,
                    ROLE_SEED,
                    getMetadataString(c.metadata(), "text", ""),
                    c.score(),
                    getMetadataString(c.metadata(), "title", null),
                    getMetadataString(c.metadata(), "block_type", "unknown"),
                    getMetadataString(c.metadata(), "semantic_role", "unknown"),
                    resolveSeedDocumentId(c, segToDocuments),
                    null,
                    safeJsonParse(getMetadataString(c.metadata(), "source_refs_json", "{}")),
                    metadata,
                    routeSources,
                    c.scoreChain(),
                    "",
                    citation
            ));
        }
        // Composite stable ordering: navigated-chapter seeds first (tree navigation),
        // then nucleus-first within each tier (discourse). Relevance (rerank) order is
        // preserved within equal keys. Both signals are no-ops when absent — so
        // un-navigated / un-mined data keeps the original order.
        items.sort(Comparator
                .comparingInt((ContextItem it) -> sectionTier(it, navigatedSections))
                .thenComparingInt(it -> discourseTier(
                        getMetadataString(it.metadata(), "discourse_role", DISCOURSE_STANDALONE))));
        return items;
    }

    /** Tier for tree-navigation ordering: in-navigated-chapter(0) before others(1). */
    private static int sectionTier(ContextItem it, Set<String> navigatedSections) {
        if (navigatedSections == null || navigatedSections.isEmpty()) {
            return 0;  // no navigation → no section bias
        }
        return Boolean.TRUE.equals(it.metadata().get("in_navigated_section")) ? 0 : 1;
    }

    /** Tier for discourse-aware ordering: nucleus(0) < standalone(1) < satellite(2). */
    private static int discourseTier(String role) {
        if (DISCOURSE_NUCLEUS.equals(role)) return 0;
        if (DISCOURSE_SATELLITE.equals(role)) return 2;
        return 1;
    }

    /** Build {segmentId -&gt; discourse_role} from segment metadata_json. */
    private Map<String, String> buildDiscourseRoleMap(List<SegmentWithMetaRow> segments) {
        Map<String, String> map = new LinkedHashMap<>();
        for (var seg : segments) {
            if (seg.getId() != null) {
                map.put(seg.getId(), discourseRoleOf(seg.getMetadataJson()));
            }
        }
        return map;
    }

    /** Extract discourse_role from a segment metadata_json string; "standalone" if absent/unparseable. */
    private static String discourseRoleOf(String metadataJson) {
        if (metadataJson == null || metadataJson.isBlank()) {
            return DISCOURSE_STANDALONE;
        }
        try {
            Map<String, Object> m = MAPPER.readValue(metadataJson, new TypeReference<LinkedHashMap<String, Object>>() {});
            Object r = m.get("discourse_role");
            return r != null && !r.toString().isBlank() ? r.toString() : DISCOURSE_STANDALONE;
        } catch (Exception e) {
            return DISCOURSE_STANDALONE;
        }
    }

    /**
     * Resolve a seed's discourse role from its underlying segment(s).
     * Nucleus wins if any source segment is a nucleus; otherwise satellite only if
     * a source segment is a satellite; else standalone.
     */
    private String resolveSeedDiscourseRole(RetrievalCandidate c, Map<String, String> segDiscourseRole) {
        if (segDiscourseRole.isEmpty()) {
            return DISCOURSE_STANDALONE;
        }
        boolean sawSatellite = false;
        for (String segId : resolveCandidateSources(c)) {
            String role = segDiscourseRole.get(segId);
            if (DISCOURSE_NUCLEUS.equals(role)) {
                return DISCOURSE_NUCLEUS;
            }
            if (DISCOURSE_SATELLITE.equals(role)) {
                sawSatellite = true;
            }
        }
        return sawSatellite ? DISCOURSE_SATELLITE : DISCOURSE_STANDALONE;
    }

    /** Build {segmentId -> lower-cased top-level section title} from source segments. */
    private Map<String, String> buildSectionPrefixMap(List<SegmentWithMetaRow> segments) {
        Map<String, String> map = new LinkedHashMap<>();
        for (var seg : segments) {
            if (seg.getId() != null) {
                String prefix = TreeNavigator.prefixOf(seg.getSectionPath());
                if (prefix != null) {
                    map.put(seg.getId(), prefix);
                }
            }
        }
        return map;
    }

    /** Section prefix of a seed's first resolvable source segment; null if none. */
    private String resolveSeedSectionPrefix(RetrievalCandidate c, Map<String, String> segSectionPrefix) {
        if (segSectionPrefix.isEmpty()) {
            return null;
        }
        for (String segId : resolveCandidateSources(c)) {
            String prefix = segSectionPrefix.get(segId);
            if (prefix != null) {
                return prefix;
            }
        }
        return null;
    }

    /**
     * The document a seed came from, or null when that is not a single answer.
     *
     * <p>Seeds carried no {@code sourceId} at all before: a caller holding the item that actually
     * matched had to walk {@code citation.raw_segment_ids} and look each one up just to learn which
     * document it belonged to — while the {@code raw_segment} items around it had the id already.</p>
     *
     * <p>Null when the candidate's segments resolve to more than one document. Snapshots are
     * content-deduplicated ({@code UNIQUE (domain, normalized_content_hash)}), so identical files
     * share segments, and naming one of them would put a confident but arbitrary provenance label
     * on a citation. Same rule as {@code FullTextService}.</p>
     */
    private String resolveSeedDocumentId(
            RetrievalCandidate candidate, Map<String, Set<String>> segToDocuments) {
        Set<String> docs = new LinkedHashSet<>();
        for (String segId : resolveCandidateSources(candidate)) {
            Set<String> forSeg = segToDocuments.get(segId);
            if (forSeg != null) {
                docs.addAll(forSeg);
                if (docs.size() > 1) return null;
            }
        }
        return docs.size() == 1 ? docs.iterator().next() : null;
    }

    private Map<String, Object> buildCitation(RetrievalCandidate candidate) {
        Map<String, Object> citation = new LinkedHashMap<>();
        Map<String, Object> sourceRefs = safeJsonParse(getMetadataString(candidate.metadata(), "source_refs_json", "{}"));

        @SuppressWarnings("unchecked")
        List<String> rawSegIds = (List<String>) sourceRefs.get("raw_segment_ids");
        if (rawSegIds != null && !rawSegIds.isEmpty()) {
            citation.put("raw_segment_ids", rawSegIds);
        }

        String title = getMetadataString(candidate.metadata(), "title", null);
        if (title != null) {
            citation.put("section", title);
        }

        String docSnapshotId = getMetadataString(candidate.metadata(), "document_snapshot_id", null);
        if (docSnapshotId != null) {
            citation.put("document_snapshot_id", docSnapshotId);
        }

        return citation;
    }

    // =========================================================================
    // Source resolution
    // =========================================================================

    private List<String> resolveCandidateSources(RetrievalCandidate candidate) {
        // Priority 1: source_segment_id
        String segId = getMetadataString(candidate.metadata(), "source_segment_id", null);
        if (segId != null && !segId.isBlank()) {
            return List.of(segId);
        }

        // Priority 2: source_refs_json
        String sourceRefsJson = getMetadataString(candidate.metadata(), "source_refs_json", "{}");
        List<String> segIds = parseSourceRefs(sourceRefsJson);
        if (!segIds.isEmpty()) {
            return segIds;
        }

        // Priority 3: target_ref_json
        String targetType = getMetadataString(candidate.metadata(), "target_type", "");
        String targetRefJson = getMetadataString(candidate.metadata(), "target_ref_json", "{}");
        if (!targetType.isBlank() && !targetRefJson.equals("{}")) {
            segIds = parseSourceRefs(targetRefJson);
            if (!segIds.isEmpty()) {
                return segIds;
            }
        }

        return List.of();
    }

    // =========================================================================
    // Context item building
    // =========================================================================

    private List<ContextItem> buildSourceItems(List<SegmentWithMetaRow> segments) {
        List<ContextItem> items = new ArrayList<>();
        // Dedupe by segment id: selectWithMeta LEFT JOINs asset_document_snapshot_links (1:N),
        // so a segment whose snapshot has multiple links fans out into duplicate rows.
        Set<String> seenSegIds = new HashSet<>();
        for (var seg : segments) {
            if (seg.getId() != null && !seenSegIds.add(seg.getId())) {
                continue;
            }
            Map<String, Object> metadata = new LinkedHashMap<>();
            metadata.put("discourse_role", discourseRoleOf(seg.getMetadataJson()));
            items.add(new ContextItem(
                    seg.getId(),
                    KIND_RAW_SEGMENT,
                    ROLE_CONTEXT,
                    seg.getRawText() != null ? seg.getRawText() : "",
                    0.0,
                    seg.getSnapshotTitle(),
                    seg.getBlockType() != null ? seg.getBlockType() : "unknown",
                    seg.getSemanticRole() != null ? seg.getSemanticRole() : "unknown",
                    seg.getDocumentId(),
                    null,
                    Map.of(),
                    metadata,
                    List.of(),
                    null,
                    "",
                    Map.of()
            ));
        }
        return items;
    }

    private List<ContextItem> buildExpandedItems(List<ExpandedSegmentRow> expansions) {
        List<ContextItem> items = new ArrayList<>();
        for (var exp : expansions) {
            SegmentWithMetaRow seg = exp.segment();
            String relType = exp.relationType() != null ? exp.relationType() : "";

            // Phase 2: evidence role and score from RST relation type
            String evidenceRole = RST_ROLE_MAP.getOrDefault(relType, "background");
            double score = RST_RELATION_WEIGHTS.getOrDefault(relType, 0.3);

            // Phase 3: tag contrasting items so conflict detection can find them later
            Map<String, Object> meta = new LinkedHashMap<>();
            meta.put("discourse_role", discourseRoleOf(seg.getMetadataJson()));
            if ("contrasts_with".equals(relType)) {
                meta.put("is_contrasting", true);
                meta.put("contrasts_with_seed", exp.sourceSegmentId());
            }

            items.add(new ContextItem(
                    seg.getId(),
                    KIND_RAW_SEGMENT,
                    ROLE_SUPPORT,
                    seg.getRawText() != null ? seg.getRawText() : "",
                    score,
                    seg.getSnapshotTitle(),
                    seg.getBlockType() != null ? seg.getBlockType() : "unknown",
                    seg.getSemanticRole() != null ? seg.getSemanticRole() : "unknown",
                    seg.getDocumentId(),
                    relType,
                    Map.of(),
                    meta,
                    List.of(),
                    null,
                    evidenceRole,
                    Map.of()
            ));
        }
        // Higher RST-weight relations appear first, giving them priority in the context budget
        items.sort(Comparator.comparingDouble(ContextItem::score).reversed());
        return items;
    }

    // =========================================================================
    // Source refs building
    // =========================================================================

    private List<SourceRef> buildSources(List<DocumentSourceRow> docs) {
        Set<String> seen = new HashSet<>();
        List<SourceRef> sources = new ArrayList<>();
        for (var doc : docs) {
            String docId = doc.getId();
            if (docId == null || seen.contains(docId)) continue;
            seen.add(docId);

            sources.add(new SourceRef(
                    docId,
                    doc.getDocumentKey() != null ? doc.getDocumentKey() : "",
                    doc.getTitle(),
                    doc.getRelativePath(),
                    doc.getKbId(),
                    safeJsonParse(doc.getScopeJson() != null ? doc.getScopeJson() : "{}"),
                    Map.of()
            ));
        }
        return sources;
    }

    // =========================================================================
    // Issues and suggestions
    // =========================================================================

    private List<Issue> buildIssues(List<ContextItem> items, QueryUnderstanding understanding) {
        List<Issue> issues = new ArrayList<>();
        String queryText = understanding != null ? understanding.originalQuery() : "";

        if (items.isEmpty()) {
            issues.add(new Issue(
                    ISSUE_NO_RESULT,
                    "未找到相关内容",
                    Map.of("query", queryText)
            ));
        } else if (items.stream().allMatch(item -> item.score() < 0.1)) {
            double topScore = items.stream().mapToDouble(ContextItem::score).max().orElse(0.0);
            issues.add(new Issue(
                    ISSUE_LOW_CONFIDENCE,
                    "检索结果置信度较低",
                    Map.of("top_score", topScore)
            ));
        }

        return issues;
    }

    private List<String> buildSuggestions(List<Issue> issues) {
        List<String> suggestions = new ArrayList<>();
        for (var issue : issues) {
            if (ISSUE_NO_RESULT.equals(issue.type())) {
                suggestions.add("尝试使用更通用的关键词");
            } else if (ISSUE_LOW_CONFIDENCE.equals(issue.type())) {
                suggestions.add("尝试更精确的描述或添加产品/版本约束");
            } else if (ISSUE_CONFLICTING_EVIDENCE.equals(issue.type())) {
                suggestions.add("检索到矛盾信息，建议参考多方文档进行综合判断");
            }
        }
        return suggestions;
    }

    // =========================================================================
    // Evidence groups
    // =========================================================================

    private List<EvidenceGroup> buildEvidenceGroups(
            List<ContextItem> items,
            List<ContextRelation> relations) {
        Map<String, List<String>> snapshotItems = new LinkedHashMap<>();
        for (var item : items) {
            Object snapId = item.metadata().get("document_snapshot_id");
            if (snapId instanceof String s && !s.isBlank()) {
                snapshotItems.computeIfAbsent(s, k -> new ArrayList<>()).add(item.id());
            }
        }

        if (snapshotItems.isEmpty()) {
            return List.of();
        }

        List<EvidenceGroup> groups = new ArrayList<>();
        for (var entry : snapshotItems.entrySet()) {
            Set<String> itemIdSet = new HashSet<>(entry.getValue());
            List<String> groupRelIds = new ArrayList<>();
            for (var r : relations) {
                if (itemIdSet.contains(r.fromId()) || itemIdSet.contains(r.toId())) {
                    groupRelIds.add(r.id());
                }
            }
            groups.add(new EvidenceGroup(entry.getKey(), entry.getValue(), groupRelIds));
        }
        return groups;
    }

    // =========================================================================
    // Formatting helpers
    // =========================================================================

    private String formatUnderstanding(QueryUnderstanding understanding) {
        List<String> parts = new ArrayList<>();
        parts.add("intent=" + understanding.intent());
        for (var e : understanding.entities()) {
            parts.add(e.type() + "=" + e.name());
        }
        parts.addAll(understanding.keywords());
        return String.join(" ", parts);
    }

    // =========================================================================
    // JSON parsing utilities
    // =========================================================================

    private Map<String, Object> safeJsonParse(String json) {
        if (json == null || json.isBlank() || "{}".equals(json)) {
            return Map.of();
        }
        try {
            return MAPPER.readValue(json, new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (Exception e) {
            return Map.of();
        }
    }

    @SuppressWarnings("unchecked")
    private List<String> parseSourceRefs(String json) {
        Map<String, Object> parsed = safeJsonParse(json);
        Object rawSegIds = parsed.get("raw_segment_ids");
        if (rawSegIds instanceof List<?> list) {
            List<String> result = new ArrayList<>();
            for (Object item : list) {
                if (item != null) result.add(String.valueOf(item));
            }
            return result;
        }
        return List.of();
    }

    // =========================================================================
    // Context compression
    // =========================================================================

    /**
     * Compresses items to stay within MAX_TOTAL_CHARS (≈ 3000 tokens).
     * Seeds receive 60% of the budget via hard truncation (already relevance-ranked).
     * Context/support items receive the remaining 40% via extractive summarization:
     * sentences are scored by character-bigram overlap with the query, and only the
     * most-relevant sentences are kept up to each item's per-item budget.
     */
    private static List<ContextItem> compressItems(
            List<ContextItem> items, List<String> queryKeywords, String queryText) {
        int totalChars = items.stream()
                .mapToInt(i -> i.text() != null ? i.text().length() : 0)
                .sum();
        if (totalChars <= MAX_TOTAL_CHARS) {
            return items;
        }

        List<ContextItem> seeds = new ArrayList<>();
        List<ContextItem> others = new ArrayList<>();
        for (ContextItem item : items) {
            if (ROLE_SEED.equals(item.role())) seeds.add(item);
            else others.add(item);
        }

        int seedBudget  = (int) (MAX_TOTAL_CHARS * 0.6);
        int otherBudget = MAX_TOTAL_CHARS - seedBudget;

        List<ContextItem> compressed = new ArrayList<>(items.size());
        if (!seeds.isEmpty()) {
            int perSeed = Math.max(200, seedBudget / seeds.size());
            for (ContextItem item : seeds) compressed.add(truncateText(item, perSeed));
        }
        if (!others.isEmpty()) {
            Set<String> queryBigrams = buildQueryBigrams(queryKeywords, queryText);
            int perOther = Math.max(100, otherBudget / others.size());
            for (ContextItem item : others) compressed.add(extractiveSummarize(item, perOther, queryBigrams));
        }
        return compressed;
    }

    /**
     * Truncates item text to maxChars, appending an ellipsis if truncated.
     */
    private static ContextItem truncateText(ContextItem item, int maxChars) {
        String text = item.text();
        if (text == null || text.length() <= maxChars) return item;
        return new ContextItem(
                item.id(), item.kind(), item.role(), text.substring(0, maxChars) + "...",
                item.score(), item.title(), item.blockType(), item.semanticRole(),
                item.sourceId(), item.relationToSeed(), item.sourceRefs(),
                item.metadata(), item.routeSources(), item.scoreChain(),
                item.evidenceRole(), item.citation()
        );
    }

    /**
     * Selects the most query-relevant sentences from item text up to maxChars.
     * Falls back to hard truncation when the text has only one sentence or no query context.
     * Selected sentences are returned in their original order to preserve readability.
     */
    private static ContextItem extractiveSummarize(ContextItem item, int maxChars, Set<String> queryBigrams) {
        String text = item.text();
        if (text == null || text.length() <= maxChars) return item;

        List<String> sentences = splitSentences(text);
        if (sentences.size() <= 1 || queryBigrams.isEmpty()) {
            return truncateText(item, maxChars);
        }

        record Sent(int idx, String text, double score) {}
        List<Sent> scored = new ArrayList<>();
        for (int i = 0; i < sentences.size(); i++) {
            String s = sentences.get(i);
            if (!s.isEmpty()) {
                scored.add(new Sent(i, s, bigramOverlapScore(s, queryBigrams)));
            }
        }

        // Greedy: highest-scored first, stop when budget is full
        scored.sort(Comparator.comparingDouble(Sent::score).reversed());
        List<Sent> selected = new ArrayList<>();
        int used = 0;
        for (Sent s : scored) {
            int needed = s.text().length() + (selected.isEmpty() ? 0 : 1); // +1 for separator
            if (used + needed <= maxChars) {
                selected.add(s);
                used += needed;
            }
        }
        if (selected.isEmpty()) {
            // Budget too tight for any full sentence — truncate the highest-scored one
            Sent top = scored.get(0);
            String truncated = top.text().substring(0, Math.min(top.text().length(), maxChars));
            selected.add(new Sent(top.idx(), truncated, top.score()));
        }

        // Restore original sentence order
        selected.sort(Comparator.comparingInt(Sent::idx));
        String summarized = String.join(" ", selected.stream().map(Sent::text).toList());

        return new ContextItem(
                item.id(), item.kind(), item.role(), summarized,
                item.score(), item.title(), item.blockType(), item.semanticRole(),
                item.sourceId(), item.relationToSeed(), item.sourceRefs(),
                item.metadata(), item.routeSources(), item.scoreChain(),
                item.evidenceRole(), item.citation()
        );
    }

    /** Splits text on Chinese/English sentence-ending punctuation and newlines. */
    private static List<String> splitSentences(String text) {
        String[] parts = text.split("(?<=[。！？!?])\\s*|\\n+");
        List<String> result = new ArrayList<>();
        for (String part : parts) {
            String trimmed = part.trim();
            if (!trimmed.isEmpty()) result.add(trimmed);
        }
        return result;
    }

    /** Builds a character-bigram set from the combined query text and keyword list. */
    private static Set<String> buildQueryBigrams(List<String> keywords, String queryText) {
        StringBuilder combined = new StringBuilder();
        if (queryText != null) combined.append(queryText);
        if (keywords != null) {
            for (String kw : keywords) combined.append(' ').append(kw);
        }
        return charBigrams(combined.toString().trim());
    }

    /** Returns the proportion of query bigrams that appear in the sentence (0.0 - 1.0). */
    private static double bigramOverlapScore(String sentence, Set<String> queryBigrams) {
        if (queryBigrams.isEmpty()) return 1.0;
        Set<String> sentBigrams = charBigrams(sentence);
        if (sentBigrams.isEmpty()) return 0.0;
        int matches = 0;
        for (String bg : sentBigrams) {
            if (queryBigrams.contains(bg)) matches++;
        }
        return (double) matches / queryBigrams.size();
    }

    private static Set<String> charBigrams(String text) {
        if (text == null || text.length() < 2) return Set.of();
        Set<String> bigrams = new HashSet<>();
        for (int i = 0; i < text.length() - 1; i++) {
            bigrams.add(text.substring(i, i + 2));
        }
        return bigrams;
    }

    // =========================================================================
    // Contradiction detection
    // =========================================================================

    /**
     * Scans seed items for conflicting semantic_role values (e.g. "advantage" vs
     * "disadvantage", "pro" vs "con") and flags them as an issue.
     * This is a lightweight heuristic — no LLM involved.
     */
    private void detectContradictions(List<ContextItem> seedItems, List<Issue> issues) {
        if (seedItems.size() < 2) return;

        Set<String> semanticRoles = new HashSet<>();
        for (var item : seedItems) {
            String sr = item.semanticRole();
            if (sr != null && !"unknown".equals(sr)) {
                semanticRoles.add(sr.toLowerCase());
            }
        }

        // Check for known opposing pairs
        boolean hasContradiction = false;
        if (semanticRoles.contains("advantage") && semanticRoles.contains("disadvantage")) {
            hasContradiction = true;
        } else if (semanticRoles.contains("pro") && semanticRoles.contains("con")) {
            hasContradiction = true;
        } else if (semanticRoles.contains("benefit") && semanticRoles.contains("risk")) {
            hasContradiction = true;
        }

        if (hasContradiction) {
            List<String> conflictIds = seedItems.stream()
                    .filter(it -> {
                        String sr = it.semanticRole();
                        return sr != null && !"unknown".equals(sr)
                                && (sr.equalsIgnoreCase("advantage") || sr.equalsIgnoreCase("disadvantage")
                                || sr.equalsIgnoreCase("pro") || sr.equalsIgnoreCase("con")
                                || sr.equalsIgnoreCase("benefit") || sr.equalsIgnoreCase("risk"));
                    })
                    .map(ContextItem::id)
                    .toList();
            issues.add(new Issue(
                    ISSUE_CONFLICTING_EVIDENCE,
                    "检测到矛盾信息：以下内容存在对立的语义角色，请注意辨别",
                    Map.of("conflicting_count", conflictIds.size(),
                           "conflicting_ids", conflictIds)
            ));
            log.info("[assemble] contradiction detected: {} conflicting seeds by semantic_role", conflictIds.size());
        }
    }

    // =========================================================================
    // Metadata helpers
    // =========================================================================

    private String getMetadataString(Map<String, Object> metadata, String key, String defaultValue) {
        Object value = metadata.get(key);
        if (value == null) return defaultValue;
        String str = String.valueOf(value);
        return str.isBlank() ? defaultValue : str;
    }
}
