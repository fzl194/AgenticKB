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
import org.mockito.ArgumentCaptor;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.mockito.Mockito.intThat;

/**
 * {@code fts} 算子边界（批次8 R2，25 号 §6.4）：空输入守卫、分词查询下推、hard filters
 * Top-K 前下推、canonical 聚合输出、空结果是正常结果。
 */
@DisplayName("FtsOperator")
class FtsOperatorTest {

    private AssetRetrievalUnitV2Mapper mapper;
    private FtsOperator op;
    private ExecContext ctx;

    @BeforeEach
    void setUp() {
        mapper = mock(AssetRetrievalUnitV2Mapper.class);
        op = new FtsOperator(mapper);
        ctx = new ExecContext("r", "d", "prod", false);
    }

    private static ActiveScope scope(Map<String, Object> hardFilters) {
        return new ActiveScope("rel", "b1", List.of("snap-1"), Map.of(), hardFilters);
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

    @Test
    @DisplayName("blank query / empty scope → empty candidates, no DB call")
    void guardsShortCircuit() {
        assertThat(op.execute(new SlotValues(), Params.empty(), ctx)
                .getCandidates("candidates")).isEmpty();
        assertThat(op.execute(SlotValues.of("query", "  "),
                Params.empty(), ctx).getCandidates("candidates")).isEmpty();
        assertThat(op.execute(SlotValues.of("query", "q"),
                Params.empty(), ctx).getCandidates("candidates")).isEmpty(); // null scope
        verifyNoInteractions(mapper);
    }

    @Test
    @DisplayName("query is jieba-tokenized and pushed down as a lexical query string")
    void queryTokenizedBeforeDb() {
        when(mapper.searchFtsV2(anyString(), anyList(), anyList(), anyList(), anyList(), anyList(), anyInt()))
                .thenReturn(List.of());
        SlotValues in = new SlotValues();
        in.put("query", "接入网设备功耗");
        in.put("scope", scope(Map.of()));

        op.execute(in, Params.empty(), ctx);

        verify(mapper).searchFtsV2(
                argThat(q -> q != null && !q.isBlank() && !q.equals("接入网设备功耗")),
                eq(List.of("snap-1")), anyList(), anyList(), anyList(), anyList(), anyInt());
    }

    @Test
    @DisplayName("hard filters are pushed down before Top-K (jsonb params + typed lists)")
    void filtersPushedDown() {
        when(mapper.searchFtsV2(anyString(), anyList(), anyList(), anyList(), anyList(), anyList(), anyInt()))
                .thenReturn(List.of());
        SlotValues in = new SlotValues();
        in.put("query", "功耗");
        in.put("scope", scope(Map.of(
                "document_refs", List.of("doc-1"),
                "evidence_types", List.of("prose"))));

        op.execute(in, Params.empty(), ctx);

        verify(mapper).searchFtsV2(
                anyString(), eq(List.of("snap-1")),
                eq(List.of("{\"document\":\"doc-1\"}")),
                eq(List.of("prose")),
                eq(List.of()), eq(List.of()),
                intThat(limit -> limit > 0));
    }

    @Test
    @DisplayName("rows aggregate by canonical; empty rows are a normal empty result")
    void canonicalAggregationAndEmptyResult() {
        when(mapper.searchFtsV2(anyString(), anyList(), anyList(), anyList(), anyList(), anyList(), anyInt()))
                .thenReturn(List.of(
                        row("rep-a", "ev-1", 0.9),
                        row("rep-b", "ev-1", 0.8),
                        row("rep-c", "ev-2", 0.7)))
                .thenReturn(List.of());
        SlotValues in = new SlotValues();
        in.put("query", "功耗");
        in.put("scope", scope(Map.of()));

        var out = op.execute(in, Params.empty(), ctx).getCandidates("candidates");
        assertThat(out).hasSize(2);
        assertThat(out.get(0).channelId()).isEqualTo("fts");
        assertThat(out.get(0).channelRank()).isEqualTo(1);
        assertThat(ctx.attributes().get("ftsCanonicalCount")).isEqualTo(2);

        var second = op.execute(in, Params.empty(), ctx).getCandidates("candidates");
        assertThat(second).isEmpty(); // normal empty, not an error
    }

    @Test
    @DisplayName("param schema no longer declares textKind (dense-only legacy) — fts has only topK")
    void paramSchemaShape() {
        assertThat(op.definition().paramSchemaJson()).contains("topK");
        assertThat(op.definition().paramSchemaJson()).doesNotContain("textKind");
    }
}
