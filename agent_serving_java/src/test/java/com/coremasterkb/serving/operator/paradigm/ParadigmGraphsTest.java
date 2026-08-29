package com.coremasterkb.serving.operator.paradigm;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The single implementation of "what does this graph scope to" and "can its result be served".
 *
 * <p>Both behaviours were previously duplicated in {@code ParadigmBindingService} and
 * {@code ScopeResolver}. The cases here are the union of what those two implementations handled,
 * so this suite is what guarantees the merge changed nothing — including the one place they had
 * already diverged (normalization, see {@link KbIds#normalizesLikeTheRuntime()}).</p>
 */
@DisplayName("ParadigmGraphs")
class ParadigmGraphsTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    private static JsonNode graph(String json) {
        try {
            return JSON.readTree(json);
        } catch (Exception e) {
            throw new IllegalArgumentException("bad test fixture", e);
        }
    }

    @Nested
    @DisplayName("kbIdsOf")
    class KbIds {

        @Test
        @DisplayName("collects from a single scope_resolve node")
        void collectsFromOneNode() {
            assertThat(ParadigmGraphs.kbIdsOf(graph("""
                    {"nodes":[{"nodeId":"sc","operatorType":"scope_resolve",
                               "params":{"kbIds":["kb-a","kb-b"]}}]}""")))
                    .containsExactly("kb-a", "kb-b");
        }

        @Test
        @DisplayName("merges every scope_resolve node — a graph may legitimately have several")
        void mergesAcrossNodes() {
            assertThat(ParadigmGraphs.kbIdsOf(graph("""
                    {"nodes":[{"operatorType":"scope_resolve","params":{"kbIds":["kb-b"]}},
                              {"operatorType":"dense_vector","params":{"kbIds":["kb-ignored"]}},
                              {"operatorType":"scope_resolve","params":{"kbIds":["kb-a"]}}]}""")))
                    .containsExactly("kb-a", "kb-b");
        }

        /**
         * The behaviour that had already drifted: only ParadigmBindingService normalized. It stayed
         * invisible because KbAccessService.authorize re-normalizes its input — which is exactly why
         * it needs a test of its own rather than being left to a caller to guarantee.
         */
        @Test
        @DisplayName("normalizes like the runtime: trims, drops blanks, de-duplicates, sorts")
        void normalizesLikeTheRuntime() {
            assertThat(ParadigmGraphs.kbIdsOf(graph("""
                    {"nodes":[{"operatorType":"scope_resolve",
                               "params":{"kbIds":["  kb-z  ","kb-a","kb-z","   ",""]}}]}""")))
                    .containsExactly("kb-a", "kb-z");
        }

        @Test
        @DisplayName("ignores non-textual kbIds entries rather than stringifying them")
        void ignoresNonTextualEntries() {
            assertThat(ParadigmGraphs.kbIdsOf(graph("""
                    {"nodes":[{"operatorType":"scope_resolve",
                               "params":{"kbIds":["kb-a",42,null,{"id":"kb-b"}]}}]}""")))
                    .containsExactly("kb-a");
        }

        @Test
        @DisplayName("empty for a graph that scopes no knowledge base")
        void emptyWhenUnscoped() {
            assertThat(ParadigmGraphs.kbIdsOf(graph("""
                    {"nodes":[{"operatorType":"scope_resolve","params":{}},
                              {"operatorType":"assemble"}]}""")))
                    .isEmpty();
        }

        @Test
        @DisplayName("tolerates malformed graphs instead of throwing")
        void tolerationOfMalformedGraphs() {
            // ScopeResolver's copy was null-tolerant, ParadigmBindingService's was not. The merged
            // version keeps the tolerant behaviour: a caller reading a stored graph should get an
            // empty scope (which downstream rejects as empty_scope) rather than an NPE.
            assertThat(ParadigmGraphs.kbIdsOf(null)).isEmpty();
            assertThat(ParadigmGraphs.kbIdsOf(graph("{}"))).isEmpty();
            assertThat(ParadigmGraphs.kbIdsOf(graph("""
                    {"nodes":"not-an-array"}"""))).isEmpty();
            assertThat(ParadigmGraphs.kbIdsOf(graph("""
                    {"nodes":[{"operatorType":"scope_resolve","params":{"kbIds":"kb-a"}}]}""")))
                    .isEmpty();
            assertThat(ParadigmGraphs.kbIdsOf(graph("""
                    {"nodes":[{"params":{"kbIds":["kb-a"]}}]}"""))).isEmpty();
        }

        @Test
        @DisplayName("returns an immutable list — callers must not be able to widen a scope in place")
        void returnsImmutableList() {
            List<String> ids = ParadigmGraphs.kbIdsOf(graph("""
                    {"nodes":[{"operatorType":"scope_resolve","params":{"kbIds":["kb-a"]}}]}"""));
            assertThat(ids).isUnmodifiable();
        }
    }

    @Nested
    @DisplayName("isServable / outputSlotOf")
    class Servable {

        @Test
        @DisplayName("assemble terminus (evidenceResponse) is servable")
        void assembleIsServable() {
            JsonNode g = graph("""
                    {"nodes":[{"nodeId":"asm","operatorType":"assemble"}],
                     "output":{"nodeId":"asm","slot":"evidenceResponse"}}""");
            assertThat(ParadigmGraphs.isServable(g)).isTrue();
            assertThat(ParadigmGraphs.outputSlotOf(g)).isEqualTo("evidenceResponse");
        }

        @Test
        @DisplayName("the retired contextPack terminus is no longer servable")
        void contextPackIsNotServable() {
            JsonNode g = graph("""
                    {"nodes":[{"nodeId":"asm","operatorType":"assemble"}],
                     "output":{"nodeId":"asm","slot":"contextPack"}}""");
            assertThat(ParadigmGraphs.isServable(g)).isFalse();
        }

        @Test
        @DisplayName("a candidates terminus is not servable — bare candidates are evaluation-only")
        void collectIsNotServable() {
            JsonNode g = graph("""
                    {"nodes":[{"nodeId":"out","operatorType":"collect"}],
                     "output":{"nodeId":"out","slot":"candidates"}}""");
            assertThat(ParadigmGraphs.isServable(g)).isFalse();
            // The slot is reported so a rejecting caller can say what it found, not just what it wanted.
            assertThat(ParadigmGraphs.outputSlotOf(g)).isEqualTo("candidates");
        }

        @Test
        @DisplayName("missing or malformed output declaration is not servable, and reports no slot")
        void missingOutputIsNotServable() {
            for (String json : List.of("{}", """
                    {"output":{}}""", """
                    {"output":{"slot":null}}""", """
                    {"output":"evidenceResponse"}""")) {
                JsonNode g = graph(json);
                assertThat(ParadigmGraphs.isServable(g)).as(json).isFalse();
                assertThat(ParadigmGraphs.outputSlotOf(g)).as(json).isNull();
            }
            assertThat(ParadigmGraphs.isServable(null)).isFalse();
            assertThat(ParadigmGraphs.outputSlotOf(null)).isNull();
        }
    }
}
