package com.coremasterkb.serving.operator.operators.query;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.infrastructure.EmbeddingClient;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotValues;
import com.coremasterkb.serving.operator.core.exceptions.OperatorException;
import com.coremasterkb.serving.operator.mapper.AssetRetrievalUnitV2Mapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

/**
 * {@code query_embed} 契约（批次8 R2，25 号 §6.3）：单次嵌入、活动 Build 维度相容校验
 * （不相容明确失败）、dim/latency 可观测。
 */
@DisplayName("QueryEmbedOperator")
class QueryEmbedOperatorTest {

    private EmbeddingClient embeddingClient;
    private AssetRetrievalUnitV2Mapper mapper;
    private QueryEmbedOperator op;
    private ExecContext ctx;

    @BeforeEach
    void setUp() {
        embeddingClient = mock(EmbeddingClient.class);
        mapper = mock(AssetRetrievalUnitV2Mapper.class);
        op = new QueryEmbedOperator(embeddingClient, mapper);
        ctx = new ExecContext("r", "d", "prod", false);
        when(embeddingClient.isConfigured()).thenReturn(true);
    }

    private static SlotValues queryOnly(String q) {
        return SlotValues.of("query", q);
    }

    @Test
    @DisplayName("embeds the query exactly once and returns the vector")
    void singleEmbed() {
        float[] vec = new float[4];
        when(embeddingClient.embed(anyString())).thenReturn(vec);

        var out = op.execute(queryOnly("接入网功耗"), Params.empty(), ctx);

        assertThat(out.getVector("queryEmbedding")).isSameAs(vec);
        verify(embeddingClient, times(1)).embed("接入网功耗");
    }

    @Test
    @DisplayName("records dim/latency observability attributes")
    void observabilityAttributes() {
        when(embeddingClient.embed(anyString())).thenReturn(new float[4]);

        op.execute(queryOnly("q"), Params.empty(), ctx);

        assertThat(ctx.attributes().get("queryEmbedDim")).isEqualTo(4);
        assertThat(ctx.attributes()).containsKey("queryEmbedLatencyMs");
    }

    @Test
    @DisplayName("dimension compatible with active build profile → passes")
    void dimensionCompatible() {
        when(embeddingClient.embed(anyString())).thenReturn(new float[1024]);
        when(mapper.selectDistinctDimensions(anyList())).thenReturn(List.of(1024));
        SlotValues in = queryOnly("q");
        in.put("scope", new ActiveScope("rel", "b1", List.of("snap-1"), Map.of()));

        var out = op.execute(in, Params.empty(), ctx);

        assertThat(out.getVector("queryEmbedding")).hasSize(1024);
    }

    @Test
    @DisplayName("dimension incompatible with active build profile → explicit failure, no re-embed loop")
    void dimensionIncompatibleFailsExplicitly() {
        when(embeddingClient.embed(anyString())).thenReturn(new float[768]);
        when(mapper.selectDistinctDimensions(anyList())).thenReturn(List.of(1024));
        SlotValues in = queryOnly("q");
        in.put("scope", new ActiveScope("rel", "b1", List.of("snap-1"), Map.of()));

        assertThatThrownBy(() -> op.execute(in, Params.empty(), ctx))
                .isInstanceOf(OperatorException.class)
                .hasMessageContaining("dimension mismatch");
        // 单次嵌入后即失败——不为其他 representation 循环生成向量
        verify(embeddingClient, times(1)).embed(anyString());
    }

    @Test
    @DisplayName("no vector data in scope → check skipped (dense reports capability later)")
    void noProfileDimsSkipsCheck() {
        when(embeddingClient.embed(anyString())).thenReturn(new float[768]);
        when(mapper.selectDistinctDimensions(anyList())).thenReturn(List.of());
        SlotValues in = queryOnly("q");
        in.put("scope", new ActiveScope("rel", "b1", List.of("snap-1"), Map.of()));

        var out = op.execute(in, Params.empty(), ctx);

        assertThat(out.getVector("queryEmbedding")).hasSize(768);
    }

    @Test
    @DisplayName("no scope input (lexical/eval graph) → embed without profile check")
    void optionalScope() {
        when(embeddingClient.embed(anyString())).thenReturn(new float[3]);

        var out = op.execute(queryOnly("q"), Params.empty(), ctx);

        assertThat(out.getVector("queryEmbedding")).hasSize(3);
        verify(mapper, never()).selectDistinctDimensions(anyList());
    }

    @Test
    @DisplayName("empty query / unconfigured service / no vector → explicit failures")
    void failureGuards() {
        assertThatThrownBy(() -> op.execute(queryOnly("  "), Params.empty(), ctx))
                .isInstanceOf(OperatorException.class).hasMessageContaining("empty query");

        when(embeddingClient.isConfigured()).thenReturn(false);
        assertThatThrownBy(() -> op.execute(queryOnly("q"), Params.empty(), ctx))
                .isInstanceOf(OperatorException.class).hasMessageContaining("not configured");

        when(embeddingClient.isConfigured()).thenReturn(true);
        when(embeddingClient.embed(anyString())).thenReturn(null);
        assertThatThrownBy(() -> op.execute(queryOnly("q"), Params.empty(), ctx))
                .isInstanceOf(OperatorException.class).hasMessageContaining("no vector");
    }
}
