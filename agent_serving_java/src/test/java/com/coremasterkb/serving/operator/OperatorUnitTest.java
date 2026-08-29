package com.coremasterkb.serving.operator;

import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.domain.ScoreChain;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotValues;
import com.coremasterkb.serving.operator.operators.fuse.RrfOperator;
import com.coremasterkb.serving.operator.operators.retrieve.DenseVectorOperator;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Per-operator unit tests (PRD §14.1) for the dependency-free / guard paths: parameter boundaries
 * and empty-input handling. No DB or LLM involved. 批次8 R0 后仅覆盖存留的无依赖算子
 * （rrf / dense_vector 的空输入守卫）。
 */
class OperatorUnitTest {

    private static final ObjectMapper M = new ObjectMapper();

    private static Params params(String json) {
        try { return new Params(M.readTree(json)); } catch (Exception e) { throw new RuntimeException(e); }
    }
    private static ExecContext ctx() { return new ExecContext("r", "d", "prod", false); }
    private static RetrievalCandidate c(String uid, double score, String source) {
        return new RetrievalCandidate(uid, score, source, Map.of(),
                new ScoreChain(score, 0.0, 0.0, List.of(source)));
    }
    private static SlotValues in(String slot, Object val) { return SlotValues.of(slot, val); }

    @Test
    void rrfRanksSharedUnitFirst() {
        // u2 appears in both source groups → highest fused score
        var merged = List.of(
                c("u1", 0.9, "x"), c("u2", 0.8, "x"),
                c("u2", 0.95, "y"), c("u3", 0.7, "y"));
        var out = new RrfOperator().execute(in("candidates", merged), params("{\"k\":60}"), ctx());
        var r = out.getCandidates("candidates");
        assertEquals(3, r.size());
        assertEquals("u2", r.get(0).retrievalUnitId());
    }

    @Test
    void denseVectorReturnsEmptyWithoutEmbedding() {
        // null mapper is never touched: missing queryEmbedding short-circuits to empty
        var out = new DenseVectorOperator(null).execute(new SlotValues(), params("{}"), ctx());
        assertTrue(out.getCandidates("candidates").isEmpty());
    }
}
