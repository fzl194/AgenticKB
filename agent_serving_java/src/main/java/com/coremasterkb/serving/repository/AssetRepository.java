package com.coremasterkb.serving.repository;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.entity.AssetBuildDocumentSnapshot;
import com.coremasterkb.serving.mapper.AssetBuildDocumentSnapshotMapper;
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

    private final AssetBuildDocumentSnapshotMapper buildSnapshotMapper;

    public AssetRepository(AssetBuildDocumentSnapshotMapper buildSnapshotMapper) {
        this.buildSnapshotMapper = buildSnapshotMapper;
    }

    // -------------------------------------------------------------------------
    // Active scope resolution
    // -------------------------------------------------------------------------

    /**
     * Resolve scope from KB builds. This is the only production retrieval scope.
     *
     * <p>This resolves each KB document's current snapshot from the newest build of its own KB, matching the
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

}
