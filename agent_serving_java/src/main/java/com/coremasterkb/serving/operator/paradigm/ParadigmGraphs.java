package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.domain.ActiveScope;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Reads out of a paradigm graph the two properties that components outside the executor care about:
 * which knowledge bases it scopes to, and whether its result can be served to a caller.
 *
 * <p><b>Why this class exists.</b> Both walks previously existed in duplicate — bind-time
 * drill-down scope resolution
 * ({@code ScopeResolver}) each carried their own copy — and they had already drifted: only the
 * former normalized the kbIds it collected. The drift was invisible because
 * {@code KbAccessService.authorize} re-normalizes its input, so both happened to behave the same;
 * the next divergence would not have been so lucky.</p>
 *
 * <p>{@link #isServable(JsonNode)} lives here for a sharper reason: binding and the MCP catalog
 * must agree on what "servable" means. If the two definitions drift, a paradigm can be bindable yet
 * missing from the catalog — a contradiction with no obvious place to look.</p>
 *
 * <p>Pure graph reading: no DB access, no {@code DomainContext}, safe to call from any thread.</p>
 */
public final class ParadigmGraphs {

    private static final String SCOPE_RESOLVE = "scope_resolve";

    /** The only output slot whose value is a ContextPack, i.e. the only servable terminus. */
    private static final String CONTEXT_PACK_SLOT = "contextPack";

    private ParadigmGraphs() {
    }

    /**
     * Collect kbIds from every {@code scope_resolve} node — a graph may legitimately have several.
     *
     * <p>Normalized via {@link ActiveScope#normalizeKbIds} before returning, so every caller sees
     * exactly the list execution will see. Callers that pass the result to
     * {@code KbAccessService.authorize} get normalized twice, which is harmless and deliberate:
     * the guarantee belongs to both, and neither should have to trust the other for it.</p>
     *
     * @param graph a compiled-source paradigm graph; null yields an empty list
     * @return trimmed, de-duplicated, sorted kb ids; empty when the graph scopes no knowledge base
     */
    public static List<String> kbIdsOf(JsonNode graph) {
        JsonNode nodes = (graph != null) ? graph.get("nodes") : null;
        if (nodes == null || !nodes.isArray()) {
            return List.of();
        }
        Set<String> collected = new LinkedHashSet<>();
        for (JsonNode node : nodes) {
            JsonNode type = node.get("operatorType");
            if (type == null || !SCOPE_RESOLVE.equals(type.asText())) {
                continue;
            }
            JsonNode params = node.get("params");
            JsonNode ids = (params != null) ? params.get("kbIds") : null;
            if (ids == null || !ids.isArray()) {
                continue;
            }
            for (JsonNode id : ids) {
                if (id.isTextual() && !id.asText().isBlank()) {
                    collected.add(id.asText().trim());
                }
            }
        }
        return ActiveScope.normalizeKbIds(new ArrayList<>(collected));
    }

    /**
     * The graph's declared output slot, or null when it declares none.
     *
     * <p>Exposed alongside {@link #isServable(JsonNode)} so a rejecting caller can name the slot it
     * actually found — "must end in assemble" is far less useful than "found output slot: collect".</p>
     */
    public static String outputSlotOf(JsonNode graph) {
        JsonNode output = (graph != null) ? graph.get("output") : null;
        return (output != null && output.hasNonNull("slot")) ? output.get("slot").asText() : null;
    }

    /**
     * Whether this graph's result can be served to a caller — i.e. it terminates in {@code assemble}
     * (output slot {@code contextPack}).
     *
     * <p>{@code collect} exists for the evaluation harness: it returns bare candidates that never
     * went through {@code ContextAssembler}'s source drill-down, graph expansion, evidence grouping
     * and compression. Serving those is both lower quality and a different response shape.</p>
     */
    public static boolean isServable(JsonNode graph) {
        return CONTEXT_PACK_SLOT.equals(outputSlotOf(graph));
    }
}
