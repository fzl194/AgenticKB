package com.coremasterkb.serving.application;

import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.FullTextRequest;
import com.coremasterkb.serving.domain.FullTextResponse;
import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.mapper.param.SegmentWindow;
import com.coremasterkb.serving.mapper.result.DocumentFileRow;
import com.coremasterkb.serving.mapper.result.FtsResultRow;
import com.coremasterkb.serving.mapper.result.SegmentFullRow;
import com.coremasterkb.serving.repository.AssetRepository;
import com.coremasterkb.serving.util.JsonUtils;
import com.fasterxml.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * Drill down from a retrieval result to the underlying uncompressed text.
 *
 * <p>Why this exists: {@code ContextAssembler} works to a fixed character budget — seed items are
 * hard-truncated and the rest are extractively summarised — so the {@code text} a caller receives
 * is not what is stored. The ids in that same result are enough to fetch the real thing, and this
 * service is the only supported way to do it.</p>
 *
 * <h2>The invariant</h2>
 * <p>Every segment or unit returned must live in a snapshot belonging to the scope this request
 * resolved, and a scope that resolves to zero snapshots is an error rather than an unfiltered
 * read. Unlike retrieval — where ids are produced by a query that was already scope-filtered —
 * here the ids arrive from the caller, so the scope is the <em>only</em> thing standing between a
 * guessed id and someone else's knowledge base.</p>
 */
@Service
public class FullTextService {

    private static final Logger log = LoggerFactory.getLogger(FullTextService.class);

    /** Cap on refs per request. Batching exists to avoid N round-trips, not to allow scraping. */
    public static final int MAX_REFS = 50;

    private final AssetRepository assetRepository;
    private final ScopeResolver scopeResolver;
    private final String defaultDomain;

    public FullTextService(
            AssetRepository assetRepository,
            ScopeResolver scopeResolver,
            ServingProperties properties) {
        this.assetRepository = assetRepository;
        this.scopeResolver = scopeResolver;
        this.defaultDomain = properties.defaultDomain();
    }

    // =========================================================================
    // Public entry points
    // =========================================================================

    /**
     * Fetch the full text behind a batch of refs.
     *
     * @param username caller identity from {@code X-KB-User}; null = anonymous (public KBs only)
     */
    public FullTextResponse fetch(FullTextRequest request, String username) {
        List<FullTextRequest.Ref> refs = request.refs();
        if (refs == null || refs.isEmpty()) {
            throw new IllegalArgumentException("refs_required");
        }
        if (refs.size() > MAX_REFS) {
            throw new IllegalArgumentException("too_many_refs");
        }

        String domain = effectiveDomain(request.domain());
        DomainContext.set(domain);
        try {
            ActiveScope scope = scopeResolver.resolve(
                    domain, request.channel(), request.paradigmId(), request.paradigmVersion(),
                    request.kbIds(), username);

            List<String> unitIds = refs.stream()
                    .filter(r -> FullTextRequest.TYPE_RETRIEVAL_UNIT.equals(r.type()))
                    .map(FullTextRequest.Ref::id).distinct().toList();
            List<String> segmentIds = refs.stream()
                    .filter(r -> FullTextRequest.TYPE_RAW_SEGMENT.equals(r.type()))
                    .map(FullTextRequest.Ref::id).distinct().toList();

            Map<String, FtsResultRow> units = byId(
                    assetRepository.resolveUnitsFull(unitIds, scope.snapshotIds()));

            // A unit's own segments are fetched in the same round-trip as directly-requested ones.
            Map<String, List<String>> unitSegmentIds = new LinkedHashMap<>();
            Set<String> allSegmentIds = new LinkedHashSet<>(segmentIds);
            for (FtsResultRow unit : units.values()) {
                List<String> ids = resolveUnitSegmentIds(unit);
                unitSegmentIds.put(unit.getId(), ids);
                allSegmentIds.addAll(ids);
            }

            Map<String, SegmentFullRow> segments = allSegmentIds.isEmpty()
                    ? Map.of()
                    : bySegmentId(assetRepository.resolveSegmentsFull(
                            List.copyOf(allSegmentIds), scope.snapshotIds()));

            // Neighbours are resolved against the segments that survived the scope filter, so a
            // window can never reach outside what the caller may already read.
            List<SegmentFullRow> neighbours = request.wantsWindow()
                    ? loadNeighbours(segments.values(), request.windowRadius(), scope)
                    : List.of();

            // Document attribution comes from the scope's own map, not from a SQL join: the link
            // table is 1:N (snapshots are content-deduplicated across documents), and resolving it
            // this way both de-duplicates and confines attribution to documents in scope.
            Map<String, List<String>> snapshotToDocuments = invert(scope.documentSnapshotMap());
            List<SegmentFullRow> allRows = new ArrayList<>(segments.values());
            allRows.addAll(neighbours);
            Map<String, DocumentFileRow> files =
                    loadFileLocations(allRows, snapshotToDocuments, scope);

            List<FullTextResponse.Item> items = new ArrayList<>(refs.size());
            int hits = 0;
            for (FullTextRequest.Ref ref : refs) {
                FullTextResponse.Item item = buildItem(
                        ref, units, unitSegmentIds, segments, neighbours,
                        request.wantsWindow() ? request.windowRadius() : 0,
                        snapshotToDocuments, files);
                if (item.found()) hits++;
                items.add(item);
            }

            log.info("[fulltext] domain={} user={} refs={} hits={} granularity={} snapshots={}",
                    domain, username != null ? username : "<anonymous>",
                    refs.size(), hits, request.granularity(), scope.snapshotIds().size());

            return new FullTextResponse(
                    new FullTextResponse.ScopeInfo(
                            scope.releaseId(), scope.buildId(), scope.snapshotIds().size()),
                    items);
        } finally {
            DomainContext.clear();
        }
    }

    private String effectiveDomain(String requested) {
        return (requested != null && !requested.isBlank()) ? requested : defaultDomain;
    }

    // =========================================================================
    // Item assembly
    // =========================================================================

    private FullTextResponse.Item buildItem(
            FullTextRequest.Ref ref,
            Map<String, FtsResultRow> units,
            Map<String, List<String>> unitSegmentIds,
            Map<String, SegmentFullRow> segments,
            List<SegmentFullRow> neighbours,
            int windowRadius,
            Map<String, List<String>> snapshotToDocuments,
            Map<String, DocumentFileRow> files) {

        FullTextResponse.Unit unit = null;
        List<SegmentFullRow> targets = new ArrayList<>();

        if (FullTextRequest.TYPE_RETRIEVAL_UNIT.equals(ref.type())) {
            FtsResultRow unitRow = units.get(ref.id());
            if (unitRow == null) {
                return FullTextResponse.Item.miss(ref);
            }
            unit = new FullTextResponse.Unit(
                    unitRow.getId(), unitRow.getUnitType(), unitRow.getTitle(), unitRow.getText());
            for (String segId : unitSegmentIds.getOrDefault(ref.id(), List.of())) {
                SegmentFullRow row = segments.get(segId);
                if (row != null) targets.add(row);
            }
        } else {
            SegmentFullRow row = segments.get(ref.id());
            if (row == null) {
                return FullTextResponse.Item.miss(ref);
            }
            targets.add(row);
        }

        return FullTextResponse.Item.hit(
                ref, unit,
                buildSegments(targets, neighbours, windowRadius, snapshotToDocuments, files));
    }

    /**
     * The item's target segments, plus their neighbours in window mode, in reading order.
     *
     * <p>Ordered by {@code segment_index} rather than targets-then-neighbours so the result reads
     * as continuous prose — the whole reason to ask for a window is that the passage you want
     * straddles a segment boundary, and interleaving it out of order defeats that.</p>
     */
    private List<FullTextResponse.Segment> buildSegments(
            List<SegmentFullRow> targets,
            List<SegmentFullRow> neighbours,
            int windowRadius,
            Map<String, List<String>> snapshotToDocuments,
            Map<String, DocumentFileRow> files) {

        Map<String, SegmentFullRow> picked = new LinkedHashMap<>();
        Set<String> targetIds = new LinkedHashSet<>();
        for (SegmentFullRow t : targets) {
            picked.put(t.getId(), t);
            targetIds.add(t.getId());
        }

        if (windowRadius > 0) {
            for (SegmentFullRow candidate : neighbours) {
                if (picked.containsKey(candidate.getId())) continue;
                if (isNeighbourOfAny(candidate, targets, windowRadius)) {
                    picked.put(candidate.getId(), candidate);
                }
            }
        }

        List<SegmentFullRow> ordered = new ArrayList<>(picked.values());
        ordered.sort(Comparator
                .comparing(SegmentFullRow::getDocumentSnapshotId,
                        Comparator.nullsLast(Comparator.naturalOrder()))
                .thenComparing(SegmentFullRow::getSegmentIndex,
                        Comparator.nullsLast(Comparator.naturalOrder())));

        List<FullTextResponse.Segment> out = new ArrayList<>(ordered.size());
        for (SegmentFullRow row : ordered) {
            String role = targetIds.contains(row.getId())
                    ? "target"
                    : (precedesEveryTarget(row, targets) ? "before" : "after");
            out.add(toSegment(row, role, snapshotToDocuments, files));
        }
        return out;
    }

    /** Whether this row falls inside any target's window — same snapshot, index within radius. */
    private static boolean isNeighbourOfAny(
            SegmentFullRow candidate, List<SegmentFullRow> targets, int radius) {
        for (SegmentFullRow target : targets) {
            if (target.getSegmentIndex() == null || candidate.getSegmentIndex() == null) continue;
            if (!Objects.equals(target.getDocumentSnapshotId(), candidate.getDocumentSnapshotId())) {
                continue;
            }
            if (Math.abs(candidate.getSegmentIndex() - target.getSegmentIndex()) <= radius) {
                return true;
            }
        }
        return false;
    }

    private static boolean precedesEveryTarget(SegmentFullRow row, List<SegmentFullRow> targets) {
        for (SegmentFullRow target : targets) {
            if (target.getSegmentIndex() == null || row.getSegmentIndex() == null) continue;
            if (!Objects.equals(target.getDocumentSnapshotId(), row.getDocumentSnapshotId())) {
                continue;
            }
            if (row.getSegmentIndex() > target.getSegmentIndex()) return false;
        }
        return true;
    }

    /**
     * Fetch the neighbours of every target in one query.
     *
     * <p>Windows are built from segments that already passed the scope filter, and the mapper
     * applies that filter again, so this cannot reach content the caller could not already read —
     * it only widens within a document they can see.</p>
     */
    private List<SegmentFullRow> loadNeighbours(
            Collection<SegmentFullRow> targets, int radius, ActiveScope scope) {
        List<SegmentWindow> windows = new ArrayList<>();
        for (SegmentFullRow row : targets) {
            if (row.getSegmentIndex() == null || row.getDocumentSnapshotId() == null) continue;
            windows.add(new SegmentWindow(
                    row.getDocumentSnapshotId(),
                    Math.max(0, row.getSegmentIndex() - radius),
                    row.getSegmentIndex() + radius));
        }
        if (windows.isEmpty()) {
            return List.of();
        }
        return assetRepository.resolveSegmentWindows(windows, scope.snapshotIds());
    }

    private FullTextResponse.Segment toSegment(
            SegmentFullRow row,
            String role,
            Map<String, List<String>> snapshotToDocuments,
            Map<String, DocumentFileRow> files) {

        String documentId = soleDocumentOf(row.getDocumentSnapshotId(), snapshotToDocuments);
        DocumentFileRow file = documentId != null ? files.get(documentId) : null;

        return new FullTextResponse.Segment(
                row.getId(),
                role,
                row.getSegmentIndex(),
                row.getRawText() != null ? row.getRawText() : "",
                row.getBlockType(),
                row.getSemanticRole(),
                parseSectionPath(row.getSectionPath()),
                row.getSectionTitle() != null ? row.getSectionTitle() : row.getSnapshotTitle(),
                row.getTokenCount(),
                row.getDocumentSnapshotId(),
                documentId,
                file != null ? file.getDocumentKey() : null,
                file != null ? file.getDocumentName() : null,
                file != null ? file.getKbId() : null,
                file != null && file.hasRawFile());
    }

    /**
     * The document a snapshot belongs to, or null when that is not a single answer.
     *
     * <p>Content-level dedup means one snapshot can back several documents. Guessing one would put
     * a wrong provenance label on a citation, so ambiguity is reported as "unknown" instead.</p>
     */
    private static String soleDocumentOf(String snapshotId, Map<String, List<String>> snapshotToDocuments) {
        List<String> docs = snapshotToDocuments.get(snapshotId);
        return (docs != null && docs.size() == 1) ? docs.get(0) : null;
    }

    private Map<String, DocumentFileRow> loadFileLocations(
            Iterable<SegmentFullRow> segments,
            Map<String, List<String>> snapshotToDocuments,
            ActiveScope scope) {

        Set<String> documentIds = new LinkedHashSet<>();
        for (SegmentFullRow row : segments) {
            String docId = soleDocumentOf(row.getDocumentSnapshotId(), snapshotToDocuments);
            if (docId != null) documentIds.add(docId);
        }
        if (documentIds.isEmpty()) {
            return Map.of();
        }
        Map<String, DocumentFileRow> out = new HashMap<>();
        for (DocumentFileRow row : assetRepository.resolveFileLocations(
                List.copyOf(documentIds), scope.snapshotIds())) {
            out.put(row.getId(), row);
        }
        return out;
    }

    /**
     * A unit's underlying segments, following the same precedence the retired
     * {@code ContextAssembler.resolveCandidateSources} used, so both surfaces agree on provenance.
     */
    private static List<String> resolveUnitSegmentIds(FtsResultRow unit) {
        List<String> fromSourceRefs = JsonUtils.parseSourceRefs(unit.getSourceRefsJson());
        if (!fromSourceRefs.isEmpty()) {
            return fromSourceRefs;
        }
        // parseTargetRef, not parseSourceRefs: target_ref_json also uses the singular
        // "raw_segment_id" key, which a raw_segment_ids-only reader would silently miss.
        return JsonUtils.parseTargetRef(unit.getTargetRefJson());
    }

    private static List<String> parseSectionPath(String json) {
        if (json == null || json.isBlank()) return List.of();
        try {
            JsonNode node = JsonUtils.mapper().readTree(json);
            if (!node.isArray()) return List.of();
            List<String> out = new ArrayList<>();
            for (JsonNode part : node) {
                out.add(part.asText(""));
            }
            return out;
        } catch (Exception e) {
            return List.of();
        }
    }

    private static Map<String, FtsResultRow> byId(List<FtsResultRow> rows) {
        Map<String, FtsResultRow> out = new LinkedHashMap<>();
        for (FtsResultRow row : rows) {
            out.putIfAbsent(row.getId(), row);
        }
        return out;
    }

    private static Map<String, SegmentFullRow> bySegmentId(List<SegmentFullRow> rows) {
        Map<String, SegmentFullRow> out = new LinkedHashMap<>();
        for (SegmentFullRow row : rows) {
            out.putIfAbsent(row.getId(), row);
        }
        return out;
    }

    /** documentId → snapshotId becomes snapshotId → documentIds. */
    private static Map<String, List<String>> invert(Map<String, String> documentSnapshotMap) {
        Map<String, List<String>> out = new HashMap<>();
        for (Map.Entry<String, String> e : documentSnapshotMap.entrySet()) {
            out.computeIfAbsent(e.getValue(), k -> new ArrayList<>()).add(e.getKey());
        }
        return out;
    }
}
