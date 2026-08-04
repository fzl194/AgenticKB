package com.coremasterkb.serving.domain;

import java.util.List;

/**
 * Response of the full-text drill-down.
 *
 * <p>Per-ref misses are reported inline rather than failing the whole request: the ids came from
 * the caller's own previous result, and one stale id should not discard nineteen good lookups.
 * That is the opposite of how an unauthorized {@code kbIds} is handled — there the caller is
 * asserting a scope, and a partial answer would be indistinguishable from an empty one.</p>
 *
 * @param scope which corpus this request read
 * @param items one entry per requested ref, in request order
 */
public record FullTextResponse(ScopeInfo scope, List<Item> items) {

    public FullTextResponse {
        if (items == null) items = List.of();
    }

    /**
     * @param releaseId     the active release id, or the synthetic {@code kb:...} key for a KB scope
     * @param buildId       null for a KB scope, which spans several builds
     * @param snapshotCount how many document snapshots were readable
     */
    public record ScopeInfo(String releaseId, String buildId, int snapshotCount) {}

    /**
     * @param ref      echoed back so a caller can correlate without relying on ordering
     * @param found    false when the id is unknown, out of scope, or no longer selected
     * @param reason   only set when {@code found} is false
     * @param unit     present only for a {@code retrieval_unit} ref
     * @param segments the target segment, plus its neighbours in window mode
     */
    public record Item(
            FullTextRequest.Ref ref,
            boolean found,
            String reason,
            Unit unit,
            List<Segment> segments
    ) {
        public Item {
            if (segments == null) segments = List.of();
        }

        public static Item hit(FullTextRequest.Ref ref, Unit unit, List<Segment> segments) {
            return new Item(ref, true, null, unit, segments);
        }

        /**
         * Nonexistent, forbidden, and dropped-from-the-latest-build all report the same reason —
         * distinguishing them would turn this endpoint into an existence probe.
         */
        public static Item miss(FullTextRequest.Ref ref) {
            return new Item(ref, false, "out_of_scope", null, List.of());
        }
    }

    /** The retrieval unit's own stored text, untruncated. */
    public record Unit(String id, String unitType, String title, String text) {}

    /**
     * @param role       {@code target}, or {@code before}/{@code after} for window neighbours
     * @param hasRawFile whether the original uploaded file can be streamed for this segment's
     *                   document — false for legacy documents that never went through a KB upload
     */
    public record Segment(
            String id,
            String role,
            Integer segmentIndex,
            String text,
            String blockType,
            String semanticRole,
            List<String> sectionPath,
            String sectionTitle,
            Integer tokenCount,
            String documentSnapshotId,
            String documentId,
            String documentKey,
            String documentName,
            String kbId,
            boolean hasRawFile
    ) {
        public Segment {
            if (sectionPath == null) sectionPath = List.of();
        }
    }
}
