package com.coremasterkb.serving.operator.operators.output;

import com.coremasterkb.serving.application.ContextAssembler;
import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.ContextPack;
import com.coremasterkb.serving.domain.ContextQuery;
import com.coremasterkb.serving.domain.EvidenceNeed;
import com.coremasterkb.serving.domain.QueryUnderstanding;
import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.domain.RetrievalRoutePlan;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotDecl;
import com.coremasterkb.serving.operator.core.SlotValues;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

/**
 * {@code assemble}'s contract with paradigms that carry no {@code query_understanding} node.
 * That slot is optional precisely so a pure-vector paradigm can still terminate here — and still
 * be bindable — without paying for an LLM roundtrip it never reads.
 */
@DisplayName("AssembleOperator")
class AssembleOperatorTest {

    private ContextAssembler assembler;
    private AssembleOperator op;

    private static final ActiveScope SCOPE =
            new ActiveScope("rel", "build", List.of("snap1"), Map.of());

    @BeforeEach
    void setUp() {
        assembler = mock(ContextAssembler.class);
        op = new AssembleOperator(assembler);
        when(assembler.assemble(anyString(), any(), any(), any(), any()))
                .thenReturn(emptyPack());
    }

    private static ContextPack emptyPack() {
        return new ContextPack(
                new ContextQuery("", "", null, null, null, null, null, "rel", "build", 1),
                List.of(), List.of(), List.of(), List.of(), List.of(), List.of(), Map.of());
    }

    private static ExecContext ctx(String query) {
        ExecContext c = new ExecContext("req-1", "generic", "default", false);
        c.setQuery(query);
        return c;
    }

    private static SlotValues inputs(QueryUnderstanding understanding) {
        SlotValues v = new SlotValues();
        v.put("candidates", List.of(new RetrievalCandidate("u1", 0.9, "dense_vector",
                Map.of("text", "some text"), null)));
        v.put("scope", SCOPE);
        if (understanding != null) {
            v.put("understanding", understanding);
        }
        return v;
    }

    @Nested
    @DisplayName("slot declaration")
    class Declaration {

        @Test
        @DisplayName("understanding is optional, candidates and scope are not")
        void understandingIsOptional() {
            Map<String, SlotDecl> slots = op.definition().inputSlots().stream()
                    .collect(java.util.stream.Collectors.toMap(SlotDecl::name, s -> s));
            assertThat(slots.get("understanding").required()).isFalse();
            assertThat(slots.get("candidates").required()).isTrue();
            assertThat(slots.get("scope").required()).isTrue();
        }
    }

    @Nested
    @DisplayName("without an understanding input")
    class WithoutUnderstanding {

        @Test
        @DisplayName("executes and passes a null understanding straight through")
        void executesWithNullUnderstanding() {
            SlotValues out = op.execute(inputs(null), Params.empty(), ctx("延迟指标怎么算"));

            assertThat(out.get("contextPack")).isInstanceOf(ContextPack.class);
            verify(assembler).assemble(anyString(), isNull(), eq(SCOPE), any(), any());
        }

        @Test
        @DisplayName("falls back to the request query rather than an empty string")
        void fallsBackToRequestQuery() {
            op.execute(inputs(null), Params.empty(), ctx("延迟指标怎么算"));

            ArgumentCaptor<String> query = ArgumentCaptor.forClass(String.class);
            verify(assembler).assemble(query.capture(), isNull(), any(), any(), any());
            assertThat(query.getValue()).isEqualTo("延迟指标怎么算");
        }

        @Test
        @DisplayName("tolerates a context with no query at all")
        void toleratesNullContextQuery() {
            ExecContext noQuery = new ExecContext("req-2", "generic", "default", false);

            SlotValues out = op.execute(inputs(null), Params.empty(), noQuery);

            assertThat(out.get("contextPack")).isNotNull();
            verify(assembler).assemble(eq(""), isNull(), any(), any(), any());
        }
    }

    @Nested
    @DisplayName("with an understanding input")
    class WithUnderstanding {

        @Test
        @DisplayName("still prefers the understanding's original query")
        void prefersUnderstandingQuery() {
            var u = new QueryUnderstanding("原始问题", "general", null, null, null, null,
                    EvidenceNeed.empty(), null, "rule", null);

            op.execute(inputs(u), Params.empty(), ctx("上下文里的问题"));

            verify(assembler).assemble(eq("原始问题"), eq(u), any(), any(), any());
        }
    }

    @Nested
    @DisplayName("params")
    class ParamHandling {

        @Test
        @DisplayName("relationExpansion=false reaches the assembly config")
        void relationExpansionOff() {
            var params = new Params(new com.fasterxml.jackson.databind.ObjectMapper()
                    .createObjectNode()
                    .put("relationExpansion", false)
                    .put("maxExpanded", 0)
                    .put("maxItems", 5));

            op.execute(inputs(null), params, ctx("q"));

            ArgumentCaptor<RetrievalRoutePlan> plan = ArgumentCaptor.forClass(RetrievalRoutePlan.class);
            verify(assembler).assemble(anyString(), any(), any(), any(), plan.capture());
            assertThat(plan.getValue().assembly().relationExpansion()).isFalse();
            assertThat(plan.getValue().assembly().maxExpanded()).isZero();
            assertThat(plan.getValue().assembly().maxItems()).isEqualTo(5);
        }
    }
}
