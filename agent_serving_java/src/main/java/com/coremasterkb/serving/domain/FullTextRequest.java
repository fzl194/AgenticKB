package com.coremasterkb.serving.domain;

import com.fasterxml.jackson.annotation.JsonAlias;

import java.util.List;

/**
 * Request to fetch the uncompressed text behind ids taken from a retrieval result.
 *
 * <p>{@code paradigmId} and {@code kbIds} are two ways to say which corpus the ids came from and
 * are mutually exclusive — see {@code FullTextService#resolveScope}. Supplying neither reads the
 * domain's active release, matching a {@code /search} call without {@code kbIds}.</p>
 *
 * @param refs            what to look up (required, at most {@code FullTextService.MAX_REFS})
 * @param domain          knowledge domain; null uses the serving default
 * @param channel         release channel; null uses the domain registry's default
 * @param paradigmId      read the KB selection off this stored paradigm's {@code scope_resolve}
 * @param paradigmVersion pin a published version; null uses the paradigm's current one
 * @param kbIds           name the knowledge bases directly; authorized against the caller
 * @param granularity     {@code segment} (default) returns just the segment; {@code window} also
 *                        returns its neighbours, for when the answer straddles a segment boundary
 * @param windowRadius    how many neighbours either side in {@code window} mode; 1–5, default 1
 */
public record FullTextRequest(
        List<Ref> refs,
        String domain,
        String channel,
        @JsonAlias({"paradigm_id"}) String paradigmId,
        @JsonAlias({"paradigm_version"}) Integer paradigmVersion,
        @JsonAlias({"kb_ids"}) List<String> kbIds,
        String granularity,
        @JsonAlias({"window_radius"}) Integer windowRadius
) {
    public static final String TYPE_RETRIEVAL_UNIT = "retrieval_unit";
    public static final String TYPE_RAW_SEGMENT = "raw_segment";

    public static final String GRANULARITY_SEGMENT = "segment";
    public static final String GRANULARITY_WINDOW = "window";

    /** Bounded so one request cannot walk a whole document a few neighbours at a time. */
    public static final int MAX_WINDOW_RADIUS = 5;

    public FullTextRequest {
        if (refs == null) refs = List.of();
        if (kbIds == null) kbIds = List.of();
        if (granularity == null || granularity.isBlank()) granularity = GRANULARITY_SEGMENT;
        if (!GRANULARITY_SEGMENT.equals(granularity) && !GRANULARITY_WINDOW.equals(granularity)) {
            throw new IllegalArgumentException("unknown_granularity");
        }
        if (windowRadius == null) windowRadius = 1;
        if (windowRadius < 1 || windowRadius > MAX_WINDOW_RADIUS) {
            throw new IllegalArgumentException("window_radius_out_of_range");
        }
    }

    /** Pre-{@code granularity} arity, kept so existing callers and tests need no change. */
    public FullTextRequest(List<Ref> refs, String domain, String channel, String paradigmId,
                           Integer paradigmVersion, List<String> kbIds) {
        this(refs, domain, channel, paradigmId, paradigmVersion, kbIds, null, null);
    }

    public boolean wantsWindow() {
        return GRANULARITY_WINDOW.equals(granularity);
    }

    /**
     * One thing to look up.
     *
     * <p>{@code type} is required rather than inferred from the id: ids carry no reliable prefix,
     * and guessing wrong would silently search the wrong table and report {@code found:false}.
     * It mirrors the {@code kind} field the caller already has on each context item.</p>
     */
    public record Ref(String type, String id) {
        public Ref {
            if (id == null || id.isBlank()) {
                throw new IllegalArgumentException("ref_id_required");
            }
            if (!TYPE_RETRIEVAL_UNIT.equals(type) && !TYPE_RAW_SEGMENT.equals(type)) {
                throw new IllegalArgumentException("unknown_ref_type");
            }
        }
    }
}
