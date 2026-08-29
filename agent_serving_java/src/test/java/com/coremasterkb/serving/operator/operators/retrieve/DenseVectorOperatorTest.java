package com.coremasterkb.serving.operator.operators.retrieve;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotValues;
import com.coremasterkb.serving.operator.mapper.AssetRetrievalUnitV2Mapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * {@code dense_vector} 算子边界（批次8 R2，25 号 §6.5）：textKind/unit_type 映射删除、
 * 维度一致下推、canonical 聚合、无向量数据的 capability diagnostic（空候选+留痕）。
 */
@DisplayName("DenseVectorOperator")
class DenseVectorOperatorTest {

    private AssetRetrievalUnitV2Mapper mapper;
    private DenseVectorOperator op;
    private ExecContext ctx;

    @BeforeEach
    void setUp() {
        mapper = mock(AssetRetrievalUnitV2Mapper.class);
        op = new DenseVectorOperator(mapper);
        ctx = new ExecContext("r", "d", "prod", false);
    }

    private static ActiveScope scope() {
        return new ActiveScope("rel", "b1", List.of("snap-1"), Map.of());
    }

    private static UnitV2Row row(String repId, String canonical, double score) {
        UnitV2Row r = new UnitV2Row();
        r.setRepresentationId(repId);
        r.setSnapshotId("snap-1");
        r.setCanonicalEvidenceId(canonical);
        r.setRepresentationType("prose");
        r.setTargetType("segment");
        r.setTargetRef(canonical);
        r.setContentText("内容");
        r.setStructuralContext("");
        r.setChannelScore(score);
        return r;
    }

    private SlotValues inputs(float[] vec) {
        SlotValues in = new SlotValues();
        in.put("queryEmbedding", vec);
        in.put("scope", scope());
        return in;
    }

    @Test
    @DisplayName("missing embedding / empty scope → empty, no DB call")
    void guardsShortCircuit() {
        assertThat(op.execute(new SlotValues(), Params.empty(), ctx)
                .getCandidates("candidates")).isEmpty();
        verifyNoInteractions(mapper);
    }

    @Test
    @DisplayName("textKind param and unit_type mapping are gone; dim = query vector length")
    void noTextKindDimensionMatched() {
        when(mapper.searchDenseV2(anyString(), anyInt(), anyList(), anyList(), anyList(), anyList(), anyList(), anyInt()))
                .thenReturn(List.of());

        op.execute(inputs(new float[]{0.1f, 0.2f, 0.3f}), Params.empty(), ctx);

        assertThat(op.definition().paramSchemaJson()).doesNotContain("textKind");
        verify(mapper).searchDenseV2(
                argThat(v -> v.startsWith("[") && v.endsWith("]")),
                eq(3), eq(List.of("snap-1")),
                eq(List.of()), eq(List.of()), eq(List.of()), eq(List.of()), anyInt());
    }

    @Test
    @DisplayName("no embeddings in scope → empty candidates + capability degraded trace")
    void noVectorsCapabilityDiagnostic() {
        when(mapper.searchDenseV2(anyString(), anyInt(), anyList(), anyList(), anyList(), anyList(), anyList(), anyInt()))
                .thenReturn(List.of());
        when(mapper.selectDistinctDimensions(anyList())).thenReturn(List.of());

        var out = op.execute(inputs(new float[]{0.1f}), Params.empty(), ctx);

        assertThat(out.getCandidates("candidates")).isEmpty();
        assertThat(ctx.attributes().get("denseVectorDegraded")).isEqualTo("no_embeddings_in_scope");
        verify(mapper).selectDistinctDimensions(List.of("snap-1"));
    }

    @Test
    @DisplayName("dimension mismatch with scope profile → degraded trace (not silent empty)")
    void dimensionMismatchDiagnostic() {
        when(mapper.searchDenseV2(anyString(), anyInt(), anyList(), anyList(), anyList(), anyList(), anyList(), anyInt()))
                .thenReturn(List.of());
        when(mapper.selectDistinctDimensions(anyList())).thenReturn(List.of(1024));

        op.execute(inputs(new float[]{0.1f, 0.2f}), Params.empty(), ctx);

        assertThat(ctx.attributes().get("denseVectorDegraded")).isEqualTo("dimension_mismatch");
    }

    @Test
    @DisplayName("normal no-hit with matching dims → empty result, no degraded trace")
    void normalEmptyNoTrace() {
        when(mapper.searchDenseV2(anyString(), anyInt(), anyList(), anyList(), anyList(), anyList(), anyList(), anyInt()))
                .thenReturn(List.of());
        when(mapper.selectDistinctDimensions(anyList())).thenReturn(List.of(2));

        var out = op.execute(inputs(new float[]{0.1f, 0.2f}), Params.empty(), ctx);

        assertThat(out.getCandidates("candidates")).isEmpty();
        assertThat(ctx.attributes()).doesNotContainKey("denseVectorDegraded");
    }

    @Test
    @DisplayName("rows aggregate by canonical within the channel")
    void canonicalAggregation() {
        when(mapper.searchDenseV2(anyString(), anyInt(), anyList(), anyList(), anyList(), anyList(), anyList(), anyInt()))
                .thenReturn(List.of(
                        row("rep-a", "ev-1", 0.95),
                        row("rep-b", "ev-1", 0.90),
                        row("rep-c", "ev-2", 0.80)));

        var out = op.execute(inputs(new float[]{0.1f}), Params.empty(), ctx)
                .getCandidates("candidates");

        assertThat(out).hasSize(2);
        assertThat(out.get(0).channelId()).isEqualTo("dense");
        assertThat(out.get(0).representationRefs()).containsExactly("rep-a", "rep-b");
        assertThat(out.get(1).channelRank()).isEqualTo(2);
    }
}
