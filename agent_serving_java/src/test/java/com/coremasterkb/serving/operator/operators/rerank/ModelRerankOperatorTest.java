package com.coremasterkb.serving.operator.operators.rerank;

import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.domain.ScoreChain;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotValues;
import com.coremasterkb.serving.rerank.LlmServiceReranker;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

/**
 * {@code model_rerank} 契约（批次8 R4，25 号 §6.7）：只送 Top-N、失败原样保序 + degraded
 * 留痕、topK 截断、无阈值过滤。
 */
@DisplayName("ModelRerankOperator")
class ModelRerankOperatorTest {

    private LlmServiceReranker reranker;
    private ModelRerankOperator op;
    private ExecContext ctx;

    @BeforeEach
    void setUp() {
        reranker = mock(LlmServiceReranker.class);
        op = new ModelRerankOperator(reranker);
        ctx = new ExecContext("r", "d", "prod", false);
    }

    private static RetrievalCandidate rrfCandidate(String canonical, int rank) {
        return new RetrievalCandidate(
                "rep-" + canonical, 0.02, "rrf", Map.of(),
                new ScoreChain(0.9, 0.02, 0, List.of("fts")),
                List.of("rep-" + canonical), "prose", canonical,
                "segment", canonical, "fts", rank, 0.9, "上下文\n正文", Map.of());
    }

    private static List<RetrievalCandidate> rrfOrder(int n) {
        List<RetrievalCandidate> list = new ArrayList<>();
        for (int i = 1; i <= n; i++) {
            list.add(rrfCandidate("ev-" + i, i));
        }
        return list;
    }

    private SlotValues inputs(List<RetrievalCandidate> candidates) {
        SlotValues in = new SlotValues();
        in.put("candidates", candidates);
        in.put("query", "接入网功耗");
        return in;
    }

    @Test
    @DisplayName("empty candidates → empty output, reranker untouched")
    void emptyShortCircuit() {
        var out = op.execute(inputs(List.of()), Params.empty(), ctx);

        assertThat(out.getCandidates("candidates")).isEmpty();
        verifyNoInteractions(reranker);
    }

    @Test
    @DisplayName("reranker failure → RRF order preserved verbatim + degraded trace")
    void degradedKeepsRrfOrder() {
        List<RetrievalCandidate> rrfOrder = rrfOrder(5);
        when(reranker.rerank(any(), any())).thenReturn(null);

        var out = op.execute(inputs(rrfOrder), Params.empty(), ctx).getCandidates("candidates");

        assertThat(out).isEqualTo(rrfOrder); // 原样保序（连顺序都不动）
        assertThat(ctx.attributes().get("modelRerankDegraded"))
                .isEqualTo("reranker_unavailable_or_invalid_response");
    }

    @Test
    @DisplayName("only Top-N candidates are sent to the reranker")
    void topNSlice() {
        when(reranker.rerank(any(), any())).thenAnswer(inv -> inv.getArgument(0));

        op.execute(inputs(rrfOrder(80)), new Params(new com.fasterxml.jackson.databind.ObjectMapper()
                .createObjectNode().put("topN", 50)), ctx);

        @SuppressWarnings("unchecked")
        ArgumentCaptor<List<RetrievalCandidate>> captor =
                ArgumentCaptor.forClass(List.class);
        verify(reranker).rerank(captor.capture(), any());
        assertThat(captor.getValue()).hasSize(50);
    }

    @Test
    @DisplayName("reranked candidates keep tail (beyond Top-N) in RRF order")
    void tailAppendedAfterRerank() {
        List<RetrievalCandidate> rrfOrder = rrfOrder(5);
        // reranker 反转前 3 个（Top-N=3）
        when(reranker.rerank(any(), any())).thenAnswer(inv -> {
            List<RetrievalCandidate> ws = inv.getArgument(0);
            return List.of(ws.get(2), ws.get(1), ws.get(0));
        });
        var params = new com.fasterxml.jackson.databind.ObjectMapper().createObjectNode()
                .put("topN", 3).put("topK", 10);

        var out = op.execute(inputs(rrfOrder), new Params(params), ctx).getCandidates("candidates");

        // 头部=重排后的前3；尾部=ev-4, ev-5 原序
        assertThat(out).extracting(RetrievalCandidate::canonicalEvidenceId)
                .containsExactly("ev-3", "ev-2", "ev-1", "ev-4", "ev-5");
        assertThat(ctx.attributes()).doesNotContainKey("modelRerankDegraded");
    }

    @Test
    @DisplayName("topK limits the final output count")
    void topKTruncates() {
        when(reranker.rerank(any(), any())).thenAnswer(inv -> inv.getArgument(0));
        var params = new com.fasterxml.jackson.databind.ObjectMapper().createObjectNode()
                .put("topK", 3);

        var out = op.execute(inputs(rrfOrder(10)), new Params(params), ctx)
                .getCandidates("candidates");

        assertThat(out).hasSize(3);
        assertThat(out).extracting(RetrievalCandidate::canonicalEvidenceId)
                .containsExactly("ev-1", "ev-2", "ev-3");
    }

    @Test
    @DisplayName("threshold param is gone; topN default is 50")
    void paramSchemaShape() {
        assertThat(op.definition().paramSchemaJson()).doesNotContain("threshold");
        assertThat(op.definition().paramSchemaJson()).contains("\"topN\"");
        assertThat(op.definition().paramSchemaJson()).contains("\"default\":50");
    }
}
