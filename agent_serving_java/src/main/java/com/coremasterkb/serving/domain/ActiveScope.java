package com.coremasterkb.serving.domain;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Represents the active scoping context for a retrieval operation.
 *
 * <p>Two ways a scope gets built:</p>
 * <ul>
 *   <li><b>Release scope</b> (default): the domain's single active release →
 *       {@code releaseId}/{@code buildId} are real ids.</li>
 *   <li><b>KB scope</b>: narrowed to one or more knowledge bases, resolved from KB builds
 *       directly because KB mining runs {@code publish=false} and therefore never produce a
 *       release. There is no single release/build, so {@code releaseId} carries the synthetic
 *       key from {@link #kbScopeKey(List)} and {@code buildId} is {@code null}.</li>
 * </ul>
 *
 * <p><b>{@code releaseId} is also the semantic-cache partition key</b> (see
 * {@code SemanticCacheService#lookup}/{@code #store}). That is why the KB path must not leave it
 * null: two different KB selections would otherwise share one cache bucket and serve each other's
 * results — a cross-KB leak, not just a stale hit.</p>
 *
 * @param releaseId           release identifier, or the synthetic KB scope key
 * @param buildId             build identifier; null for KB scope (spans multiple builds)
 * @param snapshotIds         snapshot identifiers; defaults to empty list
 * @param documentSnapshotMap mapping from document id to snapshot id; defaults to empty map
 */
public record ActiveScope(
        String releaseId,
        String buildId,
        List<String> snapshotIds,
        Map<String, String> documentSnapshotMap
) {
    public ActiveScope {
        if (snapshotIds == null) snapshotIds = List.of();
        if (documentSnapshotMap == null) documentSnapshotMap = Map.of();
    }

    /**
     * Normalize a requested KB id list: trimmed, blanks dropped, de-duplicated, sorted.
     *
     * <p>Sorting matters beyond tidiness — {@link #kbScopeKey(List)} is derived from this list, so
     * {@code [a,b]} and {@code [b,a]} must produce the same cache partition.</p>
     */
    public static List<String> normalizeKbIds(List<String> kbIds) {
        if (kbIds == null || kbIds.isEmpty()) return List.of();
        Set<String> sorted = new TreeSet<>();
        for (String id : kbIds) {
            if (id == null) continue;
            String trimmed = id.trim();
            if (!trimmed.isEmpty()) sorted.add(trimmed);
        }
        return List.copyOf(sorted);
    }

    /**
     * Build the synthetic scope key for a KB-narrowed scope, e.g. {@code "kb:kb1,kb2"}.
     * Input is normalized first, so callers need not pre-sort.
     *
     * @return the key, or {@code ""} when no usable KB id was supplied
     */
    public static String kbScopeKey(List<String> kbIds) {
        List<String> normalized = normalizeKbIds(kbIds);
        if (normalized.isEmpty()) return "";
        return "kb:" + String.join(",", normalized);
    }
}
