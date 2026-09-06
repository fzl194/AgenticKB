package com.coremasterkb.serving.rerank;

import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.domain.ScoreChain;
import com.coremasterkb.serving.infrastructure.LlmClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 瘦身批次5 复审补测：LLM rerank 客户端组合链（纯单测，真机契约见 LlmServiceIntegrationTest）。
 *
 * <p>覆盖批次5 新签名 {@code rerank(query, candidates)} 下的解析语义：API 返回索引
 * 重排、未返回候选尾部追加、空白文档跳过、失败/空输入返回 null 信号降级。</p>
 */
@DisplayName("LlmServiceReranker")
class LlmServiceRerankerTest {

    private LlmClient llmClient;
    private LlmServiceReranker reranker;

    @BeforeEach
    void setUp() {
        llmClient = mock(LlmClient.class);
        reranker = new LlmServiceReranker(llmClient, 10);
    }

    private static RetrievalCandidate candidate(String id, String rankingText) {
        return new RetrievalCandidate(
                "rep-" + id, 0.1, "rrf", Map.of(),
                new ScoreChain(0.9, 0.1, 0, List.of("fts")),
                List.of("rep-" + id), "prose", "ev-" + id,
                "segment", "ev-" + id, "fts", 1, 0.9,
                rankingText, Map.of());
    }

    private static Map<String, Object> result(int index, double score) {
        Map<String, Object> m = new HashMap<>();
        m.put("index", index);
        m.put("relevance_score", score);
        return m;
    }

    @Test
    @DisplayName("API 返回索引按相关性重排，并更新 score")
    void reordersByApiIndices() {
        when(llmClient.rerank(anyString(), anyList(), anyInt())).thenReturn(Map.of(
                "results", List.of(result(1, 0.9), result(0, 0.2))));

        List<RetrievalCandidate> out = reranker.rerank("q", List.of(
                candidate("a", "甲文档"), candidate("b", "乙文档")));

        assertThat(out).extracting(RetrievalCandidate::canonicalEvidenceId)
                .containsExactly("ev-b", "ev-a");
        assertThat(out.get(0).score()).isEqualTo(0.9);
        assertThat(out.get(1).score()).isEqualTo(0.2);
    }

    @Test
    @DisplayName("API 未返回的候选按原顺序尾部追加")
    void appendsUnreturnedTail() {
        when(llmClient.rerank(anyString(), anyList(), anyInt())).thenReturn(Map.of(
                "results", List.of(result(1, 0.5))));

        List<RetrievalCandidate> out = reranker.rerank("q", List.of(
                candidate("a", "甲"), candidate("b", "乙"), candidate("c", "丙")));

        assertThat(out).extracting(RetrievalCandidate::canonicalEvidenceId)
                .containsExactly("ev-b", "ev-a", "ev-c");
    }

    @Test
    @DisplayName("空白文档被跳过且不进入 API 请求")
    void skipsBlankDocuments() {
        when(llmClient.rerank(anyString(), anyList(), anyInt())).thenReturn(Map.of(
                "results", List.of(result(0, 0.7))));

        List<RetrievalCandidate> out = reranker.rerank("q", List.of(
                candidate("blank", ""), candidate("a", "有效文本")));

        // 只有 1 条非空文档被送出（topN 参数等于实际送出数），空文档仍回填在尾部
        assertThat(out).extracting(RetrievalCandidate::canonicalEvidenceId)
                .containsExactlyInAnyOrder("ev-a", "ev-blank");
        org.mockito.Mockito.verify(llmClient).rerank(
                anyString(),
                org.mockito.ArgumentMatchers.argThat(list -> list.size() == 1),
                org.mockito.ArgumentMatchers.eq(1));
    }

    @Test
    @DisplayName("空候选 / 空查询 / API 异常 / 空结果 → null（降级信号）")
    void nullSignals() {
        assertThat(reranker.rerank("q", List.of())).isNull();
        assertThat(reranker.rerank("  ", List.of(candidate("a", "文本")))).isNull();
        assertThat(reranker.rerank(null, List.of(candidate("a", "文本")))).isNull();

        when(llmClient.rerank(anyString(), anyList(), anyInt()))
                .thenThrow(new RuntimeException("llm down"));
        assertThat(reranker.rerank("q", List.of(candidate("a", "文本")))).isNull();

        // doReturn 风格重新打桩：when() 形式会在此触发上面仍生效的 thenThrow 桩
        org.mockito.Mockito.doReturn(Map.of("results", List.of()))
                .when(llmClient).rerank(anyString(), anyList(), anyInt());
        assertThat(reranker.rerank("q", List.of(candidate("a", "文本")))).isNull();
    }
}
