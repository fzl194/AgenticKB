package com.coremasterkb.serving.domain;

import com.fasterxml.jackson.annotation.JsonAlias;

import java.util.List;
import java.util.Map;

/**
 * Top-level search request accepted by the serving API.
 *
 * <p>Note that {@code scope} and {@code kbIds} are unrelated despite the similar names.
 * {@code scope} filters on document attributes (product, version — it ends up matched against
 * {@code scope_json}); {@code kbIds} narrows which corpus is searched at all.</p>
 *
 * @param query     raw user query string (required)
 * @param scope     attribute constraints (e.g. product, version); defaults to empty map
 * @param entities  pre-recognized entities; defaults to empty list
 * @param debug     whether to include debug information in the response
 * @param domain    knowledge domain (e.g. "cloud_core_network")
 * @param channel   release channel (e.g. "prod", "staging"); null means use registry default
 * @param mode      retrieval mode; defaults to "evidence"
 * @param kbIds     restrict retrieval to these knowledge bases; empty (the default) searches the
 *                  domain's active release
 */
public record SearchRequest(
        String query,
        Map<String, Object> scope,
        List<EntityRef> entities,
        boolean debug,
        String domain,
        String channel,
        String mode,
        @JsonAlias({"kb_ids"}) List<String> kbIds
) {
    public SearchRequest {
        if (query == null || query.isBlank()) throw new IllegalArgumentException("query_required");
        if (scope == null) scope = Map.of();
        if (entities == null) entities = List.of();
        if (mode == null) mode = "evidence";
        if (kbIds == null) kbIds = List.of();
    }

    /** Pre-{@code kbIds} arity, kept so existing callers and tests need no change. */
    public SearchRequest(String query, Map<String, Object> scope, List<EntityRef> entities,
                         boolean debug, String domain, String channel, String mode) {
        this(query, scope, entities, debug, domain, channel, mode, List.of());
    }
}
