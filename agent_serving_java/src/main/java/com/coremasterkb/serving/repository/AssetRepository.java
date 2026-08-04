package com.coremasterkb.serving.repository;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.entity.AssetBuildDocumentSnapshot;
import com.coremasterkb.serving.entity.AssetPublishRelease;
import com.coremasterkb.serving.mapper.*;
import com.coremasterkb.serving.mapper.result.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Repository;

import java.util.*;

/**
 * Asset database access layer.
 * Uses plain MyBatis mapper interfaces for all database queries.
 */
@Repository
public class AssetRepository {

    private static final Logger log = LoggerFactory.getLogger(AssetRepository.class);

    private final AssetPublishReleaseMapper releaseMapper;
    private final AssetBuildDocumentSnapshotMapper buildSnapshotMapper;
    private final AssetRawSegmentMapper rawSegmentMapper;
    private final AssetRawSegmentRelationMapper relationMapper;
    private final AssetDocumentMapper documentMapper;
    private final AssetRetrievalEmbeddingMapper embeddingMapper;
    private final AssetRetrievalUnitMapper unitMapper;

    public AssetRepository(
            AssetPublishReleaseMapper releaseMapper,
            AssetBuildDocumentSnapshotMapper buildSnapshotMapper,
            AssetRawSegmentMapper rawSegmentMapper,
            AssetRawSegmentRelationMapper relationMapper,
            AssetDocumentMapper documentMapper,
            AssetRetrievalEmbeddingMapper embeddingMapper,
            AssetRetrievalUnitMapper unitMapper) {
        this.releaseMapper       = releaseMapper;
        this.buildSnapshotMapper = buildSnapshotMapper;
        this.rawSegmentMapper    = rawSegmentMapper;
        this.relationMapper      = relationMapper;
        this.documentMapper      = documentMapper;
        this.embeddingMapper     = embeddingMapper;
        this.unitMapper          = unitMapper;
    }

    // -------------------------------------------------------------------------
    // Active scope resolution
    // -------------------------------------------------------------------------

    /**
     * Resolve active release, build, and document snapshots for the given domain.
     *
     * @throws IllegalArgumentException("no_active_release") if zero active releases found
     * @throws IllegalArgumentException("multiple_active_releases") if more than 1 active releases found
     */
    public ActiveScope resolveActiveScope(String domain, String channel) {
        String effectiveDomain = (domain != null) ? domain : "default";
        String effectiveChannel = (channel != null) ? channel : "prod";

        List<AssetPublishRelease> releases = releaseMapper.selectActiveByDomain(effectiveDomain);

        // Filter by channel
        List<AssetPublishRelease> filtered = releases.stream()
                .filter(r -> effectiveChannel.equals(r.getChannel()))
                .toList();

        if (filtered.isEmpty()) {
            log.warn("No active release found: domain={}, channel={}, total_active={}",
                    effectiveDomain, effectiveChannel, releases.size());
            throw new IllegalArgumentException("no_active_release");
        }
        if (filtered.size() > 1) {
            List<String> ids = filtered.stream().map(AssetPublishRelease::getId).toList();
            log.error("Multiple active releases found: domain={}, channel={}, count={}, ids={}",
                    effectiveDomain, effectiveChannel, filtered.size(), ids);
            throw new IllegalArgumentException("multiple_active_releases");
        }

        AssetPublishRelease release = filtered.get(0);

        List<AssetBuildDocumentSnapshot> snapshots =
                buildSnapshotMapper.selectByBuildIdAndStatus(release.getBuildId(), "active");

        List<String> snapshotIds = new ArrayList<>();
        Map<String, String> documentSnapshotMap = new HashMap<>();
        for (AssetBuildDocumentSnapshot snap : snapshots) {
            if (snap.getDocumentSnapshotId() != null) {
                snapshotIds.add(snap.getDocumentSnapshotId());
            }
            if (snap.getDocumentId() != null && snap.getDocumentSnapshotId() != null) {
                documentSnapshotMap.put(snap.getDocumentId(), snap.getDocumentSnapshotId());
            }
        }

        return new ActiveScope(release.getId(), release.getBuildId(), snapshotIds, documentSnapshotMap);
    }

    /** Convenience overload: default channel = "prod". */
    public ActiveScope resolveActiveScope(String domain) {
        return resolveActiveScope(domain, "prod");
    }

    /**
     * Resolve scope, optionally narrowed to a set of knowledge bases.
     *
     * <p>Empty/null {@code kbIds} keeps the existing release-based behaviour untouched.</p>
     */
    public ActiveScope resolveActiveScope(String domain, String channel, List<String> kbIds) {
        List<String> normalized = ActiveScope.normalizeKbIds(kbIds);
        return normalized.isEmpty()
                ? resolveActiveScope(domain, channel)
                : resolveKbScope(domain, normalized);
    }

    /**
     * Resolve scope from KB builds directly, bypassing releases.
     *
     * <p>KB mining runs with {@code publish=false} so KB content never reaches an active release —
     * going through {@link #resolveActiveScope(String, String)} would always miss it. This resolves
     * each KB document's current snapshot from the newest build of its own KB, matching the
     * mining-side {@code KbDB.get_document_knowledge} contract.</p>
     *
     * <p>The resulting scope carries {@link ActiveScope#kbScopeKey(List)} as its {@code releaseId}
     * so the semantic cache partitions per KB selection instead of pooling every KB into one
     * bucket. {@code buildId} is null — a KB scope spans many builds.</p>
     *
     * @throws IllegalArgumentException("kb_ids_required") if no usable kb id was supplied
     * @throws IllegalArgumentException("no_active_kb_build") if the selection yields zero snapshots
     */
    public ActiveScope resolveKbScope(String domain, List<String> kbIds) {
        String effectiveDomain = (domain != null) ? domain : "default";
        List<String> normalized = ActiveScope.normalizeKbIds(kbIds);
        if (normalized.isEmpty()) {
            throw new IllegalArgumentException("kb_ids_required");
        }

        List<AssetBuildDocumentSnapshot> snapshots =
                buildSnapshotMapper.selectLatestKbSnapshots(effectiveDomain, normalized);

        // Documents may share an immutable snapshot (content-level dedup), so the id list needs
        // de-duplicating before it becomes an IN-list in every downstream retrieval query.
        Set<String> snapshotIds = new LinkedHashSet<>();
        Map<String, String> documentSnapshotMap = new HashMap<>();
        for (AssetBuildDocumentSnapshot snap : snapshots) {
            if (snap.getDocumentSnapshotId() != null) {
                snapshotIds.add(snap.getDocumentSnapshotId());
            }
            if (snap.getDocumentId() != null && snap.getDocumentSnapshotId() != null) {
                documentSnapshotMap.put(snap.getDocumentId(), snap.getDocumentSnapshotId());
            }
        }

        if (snapshotIds.isEmpty()) {
            log.warn("No mined content for KB scope: domain={}, kb_ids={}", effectiveDomain, normalized);
            throw new IllegalArgumentException("no_active_kb_build");
        }

        log.debug("Resolved KB scope: domain={}, kb_ids={}, documents={}, snapshots={}",
                effectiveDomain, normalized, documentSnapshotMap.size(), snapshotIds.size());

        return new ActiveScope(
                ActiveScope.kbScopeKey(normalized), null,
                new ArrayList<>(snapshotIds), documentSnapshotMap);
    }

    // -------------------------------------------------------------------------
    // Segment resolution
    // -------------------------------------------------------------------------

    public List<SegmentWithMetaRow> resolveSegmentsByIds(
            List<String> segmentIds, List<String> snapshotIds) {
        if (segmentIds == null || segmentIds.isEmpty()) {
            return Collections.emptyList();
        }
        return rawSegmentMapper.selectWithMeta(segmentIds, snapshotIds);
    }

    // -------------------------------------------------------------------------
    // Full-text drill-down
    //
    // Every method here takes ids supplied by the caller rather than ids produced by a
    // scope-filtered retrieval, so each one requires a non-empty scope and says so loudly.
    // requireScope() exists because the alternative failure mode is silent: an empty IN-list
    // would either match nothing or, in mappers that guard the filter with <if>, match
    // everything.
    // -------------------------------------------------------------------------

    /** @throws IllegalArgumentException("empty_scope") if the scope resolved to zero snapshots */
    public List<SegmentFullRow> resolveSegmentsFull(List<String> segmentIds, List<String> snapshotIds) {
        requireScope(snapshotIds);
        if (segmentIds == null || segmentIds.isEmpty()) {
            return Collections.emptyList();
        }
        return rawSegmentMapper.selectFullByIds(segmentIds, snapshotIds);
    }

    /** @throws IllegalArgumentException("empty_scope") if the scope resolved to zero snapshots */
    public List<FtsResultRow> resolveUnitsFull(List<String> unitIds, List<String> snapshotIds) {
        requireScope(snapshotIds);
        if (unitIds == null || unitIds.isEmpty()) {
            return Collections.emptyList();
        }
        return unitMapper.fetchDetailsByIdsInScope(unitIds, snapshotIds);
    }

    /** @throws IllegalArgumentException("empty_scope") if the scope resolved to zero snapshots */
    public List<DocumentFileRow> resolveFileLocations(List<String> documentIds, List<String> snapshotIds) {
        requireScope(snapshotIds);
        if (documentIds == null || documentIds.isEmpty()) {
            return Collections.emptyList();
        }
        return documentMapper.selectFileLocations(documentIds, snapshotIds);
    }

    private static void requireScope(List<String> snapshotIds) {
        if (snapshotIds == null || snapshotIds.isEmpty()) {
            throw new IllegalArgumentException("empty_scope");
        }
    }

    // -------------------------------------------------------------------------
    // Relation queries
    // -------------------------------------------------------------------------

    public List<RelationRow> getRelationsForSegments(
            List<String> segmentIds, List<String> relationTypes, List<String> snapshotIds) {
        if (segmentIds == null || segmentIds.isEmpty()) {
            return Collections.emptyList();
        }
        return relationMapper.selectRelationsForSegments(segmentIds, relationTypes, snapshotIds);
    }

    // -------------------------------------------------------------------------
    // Document source queries
    // -------------------------------------------------------------------------

    public List<DocumentSourceRow> getDocumentSources(
            List<String> documentIds, List<String> snapshotIds) {
        if (documentIds == null || documentIds.isEmpty()) {
            return Collections.emptyList();
        }
        return documentMapper.selectDocumentSources(documentIds, snapshotIds);
    }

    // -------------------------------------------------------------------------
    // Graph traversal helpers
    // -------------------------------------------------------------------------

    public List<NeighborRow> getNeighbors(
            List<String> segmentIds,
            List<String> relationTypes,
            List<String> snapshotIds) {
        if (segmentIds == null || segmentIds.isEmpty()) {
            return Collections.emptyList();
        }
        return relationMapper.selectNeighbors(segmentIds, relationTypes, snapshotIds);
    }
}
