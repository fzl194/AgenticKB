package com.coremasterkb.serving.operator;

import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.domain.ScoreChain;
import com.coremasterkb.serving.infrastructure.LlmClient;
import com.coremasterkb.serving.operator.core.*;
import com.coremasterkb.serving.operator.core.exceptions.OperatorException;
import com.coremasterkb.serving.operator.core.exceptions.ParadigmCompileException;
import com.coremasterkb.serving.operator.engine.CompileError;
import com.coremasterkb.serving.operator.engine.ParadigmCompiler;
import com.coremasterkb.serving.operator.engine.ParadigmExecutor;
import com.coremasterkb.serving.operator.engine.ParadigmGraph;
import com.coremasterkb.serving.operator.operators.fuse.RrfOperator;
import com.coremasterkb.serving.operator.registry.OperatorRegistry;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Pure unit tests for the operator DAG engine (compiler + executor) — no Spring, no DB.
 * Uses the real dependency-free {@link RrfOperator} plus mock seed/collect/throwing operators to
 * drive topology, fusion, entry binding, and error policy.
 */
class OperatorEngineTest {

    private static final ObjectMapper M = new ObjectMapper();

    // ---- mock operators -------------------------------------------------------------------

    /** No inputs; emits a fixed candidate list on slot "candidates" with a given source. */
    private static Operator seed(String type, String source, RetrievalCandidate... out) {
        return new Operator() {
            public OperatorDef definition() {
                return new OperatorDef(type, "retrieve", type, "", List.of(),
                        List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "")),
                        "{}", ErrorPolicy.FAIL_FAST);
            }
            public SlotValues execute(SlotValues in, Params p, ExecContext c) {
                return SlotValues.of("candidates", List.of(out));
            }
        };
    }

    /**
     * Test terminus standing in for the retired {@code collect} operator (批次8 R0): requires
     * candidates on input, passes them through unchanged on output.
     */
    private static Operator collect() {
        return new Operator() {
            public OperatorDef definition() {
                return new OperatorDef("collect", "output", "collect", "",
                        List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "")),
                        List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "")),
                        "{}", ErrorPolicy.FAIL_FAST);
            }
            public SlotValues execute(SlotValues in, Params p, ExecContext c) {
                return SlotValues.of("candidates", in.getCandidates("candidates"));
            }
        };
    }

    /** Reads entry slot "query"(STRING) and emits one candidate whose id echoes the query. */
    private static Operator queryEcho(String type) {
        return new Operator() {
            public OperatorDef definition() {
                return new OperatorDef(type, "query", type, "",
                        List.of(SlotDecl.required("query", SlotType.STRING, "")),
                        List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "")),
                        "{}", ErrorPolicy.FAIL_FAST);
            }
            public SlotValues execute(SlotValues in, Params p, ExecContext c) {
                return SlotValues.of("candidates", List.of(cand(in.getString("query"), 1.0, type)));
            }
        };
    }

    private static Operator throwing(String type, ErrorPolicy policy) {
        return new Operator() {
            public OperatorDef definition() {
                return new OperatorDef(type, "retrieve", type, "", List.of(),
                        List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "")),
                        "{}", policy);
            }
            public SlotValues execute(SlotValues in, Params p, ExecContext c) {
                throw new RuntimeException("boom");
            }
        };
    }

    private static RetrievalCandidate cand(String uid, double score, String source) {
        return new RetrievalCandidate(uid, score, source, Map.of(),
                new ScoreChain(score, 0.0, 0.0, List.of(source)));
    }

    private static ParadigmExecutor executor(OperatorRegistry reg) {
        return new ParadigmExecutor(reg, new LlmClient(null, null), (Runnable r) -> r.run());
    }

    private static ExecContext ctx() {
        return new ExecContext("req-1", "test_domain", "prod", false);
    }

    private static JsonNode json(String s) {
        try { return M.readTree(s); } catch (Exception e) { throw new RuntimeException(e); }
    }

    // ---- compiler tests -------------------------------------------------------------------

    @Test
    void compilesValidGraph() {
        var reg = new OperatorRegistry(List.of(seed("s", "s", cand("u1", 1, "s")), collect()));
        var compiler = new ParadigmCompiler(reg);
        ParadigmGraph g = compiler.compile(json("""
                {"nodes":[{"nodeId":"s","operatorType":"s"},{"nodeId":"out","operatorType":"collect"}],
                 "edges":[{"fromNode":"s","fromSlot":"candidates","toNode":"out","toSlot":"candidates"}],
                 "output":{"nodeId":"out","slot":"candidates"}}"""));
        assertEquals(2, g.nodes().size());
        assertEquals("out", g.outputNodeId());
    }

    @Test
    void rejectsUnknownOperator() {
        var reg = new OperatorRegistry(List.of(collect()));
        var compiler = new ParadigmCompiler(reg);
        var ex = assertThrows(ParadigmCompileException.class, () -> compiler.compile(json("""
                {"nodes":[{"nodeId":"x","operatorType":"nope"}],"edges":[],
                 "output":{"nodeId":"x","slot":"candidates"}}""")));
        assertTrue(ex.errors().stream().anyMatch(e -> e.kind().equals("unknown_operator")));
    }

    @Test
    void rejectsMissingRequiredInput() {
        // collect needs 'candidates' (not an entry slot) but has no incoming edge
        var reg = new OperatorRegistry(List.of(collect()));
        var compiler = new ParadigmCompiler(reg);
        var ex = assertThrows(ParadigmCompileException.class, () -> compiler.compile(json("""
                {"nodes":[{"nodeId":"out","operatorType":"collect"}],"edges":[],
                 "output":{"nodeId":"out","slot":"candidates"}}""")));
        assertTrue(ex.errors().stream().anyMatch(e -> e.kind().equals("missing_required_input")));
    }

    @Test
    void rejectsCycle() {
        var reg = new OperatorRegistry(List.of(new RrfOperator(), collect()));
        var compiler = new ParadigmCompiler(reg);
        // rrf -> collect -> ... no second variadic; build a 2-node cycle via two rrf nodes
        var reg2 = new OperatorRegistry(List.of(new RrfOperator()));
        var compiler2 = new ParadigmCompiler(reg2);
        var ex = assertThrows(ParadigmCompileException.class, () -> compiler2.compile(json("""
                {"nodes":[{"nodeId":"a","operatorType":"rrf"},{"nodeId":"b","operatorType":"rrf"}],
                 "edges":[{"fromNode":"a","fromSlot":"candidates","toNode":"b","toSlot":"candidates"},
                          {"fromNode":"b","fromSlot":"candidates","toNode":"a","toSlot":"candidates"}],
                 "output":{"nodeId":"a","slot":"candidates"}}""")));
        assertTrue(ex.errors().stream().anyMatch(e -> e.kind().equals("cycle")));
    }

    @Test
    void rejectsBadOutputSlot() {
        var reg = new OperatorRegistry(List.of(seed("s", "s", cand("u1", 1, "s")), collect()));
        var compiler = new ParadigmCompiler(reg);
        var ex = assertThrows(ParadigmCompileException.class, () -> compiler.compile(json("""
                {"nodes":[{"nodeId":"s","operatorType":"s"},{"nodeId":"out","operatorType":"collect"}],
                 "edges":[{"fromNode":"s","fromSlot":"candidates","toNode":"out","toSlot":"candidates"}],
                 "output":{"nodeId":"out","slot":"nonexistent"}}""")));
        assertTrue(ex.errors().stream().anyMatch(e -> e.kind().equals("bad_output")));
    }

    // ---- executor tests -------------------------------------------------------------------

    @Test
    void executesMultiRouteFusion() {
        // two seeds (sources a,b) sharing u2 -> rrf -> collect
        var reg = new OperatorRegistry(List.of(
                seed("seedA", "a", cand("u1", 0.9, "a"), cand("u2", 0.8, "a")),
                seed("seedB", "b", cand("u2", 0.95, "b"), cand("u3", 0.7, "b")),
                new RrfOperator(), collect()));
        var graph = new ParadigmCompiler(reg).compile(json("""
                {"nodes":[{"nodeId":"a","operatorType":"seedA"},{"nodeId":"b","operatorType":"seedB"},
                          {"nodeId":"f","operatorType":"rrf"},{"nodeId":"out","operatorType":"collect"}],
                 "edges":[{"fromNode":"a","fromSlot":"candidates","toNode":"f","toSlot":"candidates"},
                          {"fromNode":"b","fromSlot":"candidates","toNode":"f","toSlot":"candidates"},
                          {"fromNode":"f","fromSlot":"candidates","toNode":"out","toSlot":"candidates"}],
                 "output":{"nodeId":"out","slot":"candidates"}}"""));

        Object result = executor(reg).execute(graph, ctx(), Map.of());
        assertInstanceOf(List.class, result);
        @SuppressWarnings("unchecked")
        List<RetrievalCandidate> out = (List<RetrievalCandidate>) result;
        // u1,u2,u3 distinct; u2 appears in both routes so it ranks first
        assertEquals(3, out.size());
        assertEquals("u2", out.get(0).retrievalUnitId());
        assertEquals(List.of("a", "b"), out.get(0).scoreChain().routeSources());
    }

    @Test
    void bindsQueryEntrySlot() {
        var reg = new OperatorRegistry(List.of(queryEcho("qe"), collect()));
        var graph = new ParadigmCompiler(reg).compile(json("""
                {"nodes":[{"nodeId":"qe","operatorType":"qe"},{"nodeId":"out","operatorType":"collect"}],
                 "edges":[{"fromNode":"qe","fromSlot":"candidates","toNode":"out","toSlot":"candidates"}],
                 "output":{"nodeId":"out","slot":"candidates"}}"""));
        Object result = executor(reg).execute(graph, ctx(), Map.of("query", "hello"));
        @SuppressWarnings("unchecked")
        List<RetrievalCandidate> out = (List<RetrievalCandidate>) result;
        assertEquals(1, out.size());
        assertEquals("hello", out.get(0).retrievalUnitId());
    }

    @Test
    void skipWithEmptyDoesNotAbort() {
        var reg = new OperatorRegistry(List.of(throwing("bad", ErrorPolicy.SKIP_WITH_EMPTY), collect()));
        var graph = new ParadigmCompiler(reg).compile(json("""
                {"nodes":[{"nodeId":"bad","operatorType":"bad"},{"nodeId":"out","operatorType":"collect"}],
                 "edges":[{"fromNode":"bad","fromSlot":"candidates","toNode":"out","toSlot":"candidates"}],
                 "output":{"nodeId":"out","slot":"candidates"}}"""));
        Object result = executor(reg).execute(graph, ctx(), Map.of());
        assertInstanceOf(List.class, result);
        assertTrue(((List<?>) result).isEmpty());
    }

    @Test
    void failFastAborts() {
        var reg = new OperatorRegistry(List.of(throwing("bad", ErrorPolicy.FAIL_FAST), collect()));
        var graph = new ParadigmCompiler(reg).compile(json("""
                {"nodes":[{"nodeId":"bad","operatorType":"bad"},{"nodeId":"out","operatorType":"collect"}],
                 "edges":[{"fromNode":"bad","fromSlot":"candidates","toNode":"out","toSlot":"candidates"}],
                 "output":{"nodeId":"out","slot":"candidates"}}"""));
        assertThrows(OperatorException.class, () -> executor(reg).execute(graph, ctx(), Map.of()));
    }
}
