package com.coremasterkb.serving.operator.operators.output;

import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.domain.HydratedEvidence;
import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotValues;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 批次8 R6：{@code assemble} 的 EvidenceResponse 协议契约（25 号 §5.3/§6.9）——协议字段白名单、
 * canonical 合并、span/父子去重、预算截断与 has_more、opaque ref 稳定且不可枚举、别名文本
 * 不出现在 evidence。
 */
@DisplayName("AssembleOperator — EvidenceResponse protocol")
class AssembleOperatorTest {

    private static final ObjectMapper M = new ObjectMapper();
    private static final EvidenceRefCodec CODEC = EvidenceRefCodec.forSecret("unit-test-secret");

    private AssembleOperator op;
    private ExecContext ctx;

    @BeforeEach
    void setUp() {
        op = new AssembleOperator(CODEC);
        ctx = ctx("测试查询");
    }

    private static ExecContext ctx(String query) {
        ExecContext c = new ExecContext("req-1", "generic", "default", false);
        c.setQuery(query);
        return c;
    }

    /** 构造水合证据的测试工厂（默认无 provenance 截断标记）。 */
    private static HydratedEvidence evidence(String snapshot, String canonical, String targetType,
                                             String targetRef, String evidenceType, String parentRef,
                                             Integer ordinal, Integer windowFrom, Integer windowTo,
                                             String text, Map<String, Object> provenance,
                                             List<String> structureRefs, boolean navigable) {
        return new HydratedEvidence(snapshot, canonical, targetType, targetRef, evidenceType,
                documentOf(targetRef), parentRef, ordinal, windowFrom, windowTo,
                List.of(new HydratedEvidence.EvidenceFragment("exact", text, null, null, null)),
                "exact", structureRefs, navigable, false,
                (text == null ? 0 : (text.length() + 3) / 4),
                new HydratedEvidence.SourceProjection("kb1", "file.md", "docs/file.md",
                        documentOf(targetRef), null, null),
                provenance == null ? Map.of() : provenance);
    }

    private static String documentOf(String targetRef) {
        int hash = targetRef.indexOf('#');
        return hash > 0 ? targetRef.substring(0, hash) : targetRef;
    }

    private EvidenceResponse run(List<HydratedEvidence> evidence, ExecContext ctx) {
        SlotValues in = new SlotValues();
        in.put("hydratedEvidence", evidence);
        SlotValues out = op.execute(in, Params.empty(), ctx);
        assertThat(out.get("evidenceResponse")).isInstanceOf(EvidenceResponse.class);
        return (EvidenceResponse) out.get("evidenceResponse");
    }

    @Nested
    @DisplayName("protocol whitelist (§5.3)")
    class Protocol {

        @Test
        @DisplayName("serialized response carries exactly query/evidence/has_more")
        void topLevelWhitelist() throws Exception {
            EvidenceResponse resp = run(List.of(evidence("s1", "doc:/a#seg:1", "segment",
                    "doc:/a#seg:1", "prose", "doc:/a#section:A", 1, 1, 1,
                    "证据文本", Map.of(), List.of(), false)), ctx);

            JsonNode root = M.readTree(M.writeValueAsString(resp));
            assertThat(iterateNames(root)).containsExactlyInAnyOrder("query", "evidence", "has_more");
            assertThat(root.get("query").asText()).isEqualTo("测试查询");
            assertThat(root.get("has_more").asBoolean()).isFalse();

            JsonNode item = root.get("evidence").get(0);
            assertThat(iterateNames(item)).containsExactlyInAnyOrder(
                    "ref", "type", "content", "source", "truncated");

            JsonNode source = item.get("source");
            assertThat(iterateNames(source)).containsExactlyInAnyOrder(
                    "knowledge_base", "file_name", "relative_path", "document_ref");
            // 可选字段（section/page/structure_ref）为 null 时省略
            assertThat(source.has("section")).isFalse();
            assertThat(source.has("page")).isFalse();
            assertThat(item.has("structure_ref")).isFalse();
        }

        @Test
        @DisplayName("no internal ids / scores / ranks / channel data leak into the response")
        void noInternalLeaks() throws Exception {
            List<HydratedEvidence> input = List.of(
                    evidence("s1", "doc:/a#seg:1", "segment", "doc:/a#seg:1", "prose",
                            "doc:/a#section:A", 1, 1, 1, "证据文本",
                            Map.of("representationId", "rep-internal", "channelId", "fts"),
                            List.of("doc:/a#table:tbl:1"), true));
            EvidenceResponse resp = run(input, ctx);

            String json = M.writeValueAsString(resp);
            assertThat(json).doesNotContain("rep-internal").doesNotContain("channelId")
                    .doesNotContain("score").doesNotContain("rank")
                    .doesNotContain("doc:/a#seg:1");   // 内部结构 ref 不外泄（doc ref 已投影为 doc_）
        }

        @Test
        @DisplayName("navigable evidence carries an opaque structure_ref (st_ prefix)")
        void structureRefOpaque() throws Exception {
            List<HydratedEvidence> input = List.of(
                    evidence("s1", "doc:/a#table:tbl:1", "table", "doc:/a#table:tbl:1", "table",
                            null, null, null, null, "表格内容", Map.of(),
                            List.of("doc:/a#table:tbl:1"), true));
            EvidenceResponse resp = run(input, ctx);

            JsonNode item = M.readTree(M.writeValueAsString(resp)).get("evidence").get(0);
            assertThat(item.has("structure_ref")).isTrue();
            assertThat(item.get("structure_ref").asText()).startsWith("st_")
                    .doesNotContain("doc:/a");
        }

        private static List<String> iterateNames(JsonNode node) {
            List<String> names = new ArrayList<>();
            node.fieldNames().forEachRemaining(names::add);
            return names;
        }
    }

    @Nested
    @DisplayName("merge & dedup")
    class MergeDedup {

        @Test
        @DisplayName("same canonical collapses to one evidence, keeping the first (rerank order)")
        void canonicalDedup() {
            HydratedEvidence first = evidence("s1", "doc:/a#seg:1", "segment", "doc:/a#seg:1",
                    "prose", "doc:/a#section:A", 1, 1, 1, "第一条", Map.of(), List.of(), false);
            HydratedEvidence dup = evidence("s1", "doc:/a#seg:1", "segment", "doc:/a#seg:1",
                    "prose", "doc:/a#section:A", 1, 1, 1, "重复条", Map.of(), List.of(), false);

            EvidenceResponse resp = run(List.of(first, dup), ctx);
            assertThat(resp.evidence()).hasSize(1);
            assertThat(resp.evidence().get(0).content()).isEqualTo("第一条");
        }

        @Test
        @DisplayName("a kept section absorbs a later child segment of the same section")
        void sectionAbsorbsChild() {
            HydratedEvidence section = evidence("s1", "doc:/a#section:A", "section",
                    "doc:/a#section:A", "section", null, null, null, null, "章节全文", Map.of(),
                    List.of(), false);
            HydratedEvidence child = evidence("s1", "doc:/a#seg:2", "segment", "doc:/a#seg:2",
                    "prose", "doc:/a#section:A", 2, 2, 2, "子片段", Map.of(), List.of(), false);

            EvidenceResponse resp = run(List.of(section, child), ctx);
            assertThat(resp.evidence()).hasSize(1);
            assertThat(resp.evidence().get(0).content()).isEqualTo("章节全文");
            // 去重丢弃不是预算丢弃：has_more 不置位
            assertThat(resp.hasMore()).isFalse();
        }

        @Test
        @DisplayName("a later section absorbs an earlier kept child (parent wins on containment)")
        void laterParentAbsorbsEarlierChild() {
            HydratedEvidence child = evidence("s1", "doc:/a#seg:2", "segment", "doc:/a#seg:2",
                    "prose", "doc:/a#section:A", 2, 2, 2, "子片段", Map.of(), List.of(), false);
            HydratedEvidence section = evidence("s1", "doc:/a#section:A", "section",
                    "doc:/a#section:A", "section", null, null, null, null, "章节全文", Map.of(),
                    List.of(), false);

            EvidenceResponse resp = run(List.of(child, section), ctx);
            assertThat(resp.evidence()).hasSize(1);
            assertThat(resp.evidence().get(0).content()).isEqualTo("章节全文");
        }

        @Test
        @DisplayName("kept document absorbs every later item of the same document")
        void documentAbsorbsEverything() {
            HydratedEvidence doc = evidence("s1", "doc:/a#document", "document", "doc:/a#document",
                    "document", null, null, null, null, "整文", Map.of(), List.of(), false);
            HydratedEvidence seg = evidence("s1", "doc:/a#seg:9", "segment", "doc:/a#seg:9",
                    "prose", "doc:/a#section:B", 9, 9, 9, "片段", Map.of(), List.of(), false);

            EvidenceResponse resp = run(List.of(doc, seg), ctx);
            assertThat(resp.evidence()).hasSize(1);
            assertThat(resp.evidence().get(0).content()).isEqualTo("整文");
        }

        @Test
        @DisplayName("overlapping window covering a kept exact hit absorbs it")
        void windowOverlapAbsorbs() {
            HydratedEvidence hit = evidence("s1", "doc:/a#seg:2", "segment", "doc:/a#seg:2",
                    "prose", "doc:/a#section:A", 2, 2, 2, "命中", Map.of(), List.of(), false);
            HydratedEvidence window = evidence("s1", "doc:/a#seg:3", "segment", "doc:/a#seg:3",
                    "prose", "doc:/a#section:A", 3, 1, 5, "邻窗命中", Map.of(), List.of(), false);

            EvidenceResponse resp = run(List.of(hit, window), ctx);
            assertThat(resp.evidence()).hasSize(1);
            assertThat(resp.evidence().get(0).content()).isEqualTo("邻窗命中");
        }

        @Test
        @DisplayName("kept order follows rerank order after dedup")
        void orderPreserved() {
            HydratedEvidence a = evidence("s1", "doc:/a#seg:1", "segment", "doc:/a#seg:1",
                    "prose", "doc:/a#section:A", 1, 1, 1, "A", Map.of(), List.of(), false);
            HydratedEvidence b = evidence("s1", "doc:/b#seg:1", "segment", "doc:/b#seg:1",
                    "prose", "doc:/b#section:A", 1, 1, 1, "B", Map.of(), List.of(), false);
            HydratedEvidence c = evidence("s1", "doc:/c#seg:1", "segment", "doc:/c#seg:1",
                    "prose", "doc:/c#section:A", 1, 1, 1, "C", Map.of(), List.of(), false);

            EvidenceResponse resp = run(List.of(c, b, a), ctx);
            assertThat(resp.evidence()).extracting(EvidenceResponse.EvidenceItem::content)
                    .containsExactly("C", "B", "A");
        }
    }

    @Nested
    @DisplayName("budget (maxEvidence / maxOutputTokens)")
    class Budget {

        private HydratedEvidence seg(int n, int textLen) {
            return evidence("s1", "doc:/d" + n + "#seg:1", "segment", "doc:/d" + n + "#seg:1",
                    "prose", null, 1, 1, 1, "x".repeat(textLen), Map.of(), List.of(), false);
        }

        @Test
        @DisplayName("maxEvidence caps the list and sets has_more")
        void maxEvidenceCap() {
            SlotValues in = new SlotValues();
            in.put("hydratedEvidence", List.of(seg(1, 100), seg(2, 100), seg(3, 100)));
            Params params = new Params(M.createObjectNode().put("maxEvidence", 2).put("maxOutputTokens", 100000));
            EvidenceResponse resp = (EvidenceResponse) op.execute(in, params, ctx).get("evidenceResponse");

            assertThat(resp.evidence()).hasSize(2);
            assertThat(resp.hasMore()).isTrue();
        }

        @Test
        @DisplayName("token budget truncates the overflowing item in place (truncated=true)")
        void tokenBudgetTruncates() {
            // 一条 1000 token 的证据，预算 300 token：第一条截断保留，truncated=true
            SlotValues in = new SlotValues();
            in.put("hydratedEvidence", List.of(seg(1, 4000)));
            Params params = new Params(M.createObjectNode().put("maxOutputTokens", 300));
            EvidenceResponse resp = (EvidenceResponse) op.execute(in, params, ctx).get("evidenceResponse");

            assertThat(resp.evidence()).hasSize(1);
            assertThat(resp.evidence().get(0).truncated()).isTrue();
            assertThat(resp.evidence().get(0).content().length()).isLessThanOrEqualTo(300 * 4);
        }

        @Test
        @DisplayName("when remaining budget is too small the item is deferred with has_more")
        void tooSmallBudgetDefers() {
            // 第一条 25 token 后剩余 55 < MIN_ITEM_TOKENS(64) → 不塞半截，has_more=true
            SlotValues in = new SlotValues();
            in.put("hydratedEvidence", List.of(seg(1, 100), seg(2, 4000)));
            Params params = new Params(M.createObjectNode().put("maxOutputTokens", 80));
            EvidenceResponse resp = (EvidenceResponse) op.execute(in, params, ctx).get("evidenceResponse");

            assertThat(resp.evidence()).hasSize(1);
            assertThat(resp.evidence().get(0).truncated()).isFalse();
            assertThat(resp.hasMore()).isTrue();
        }

        @Test
        @DisplayName("hydration-time truncation (bounded section/table) marks the item truncated")
        void hydrationTruncationPropagates() {
            HydratedEvidence bounded = evidence("s1", "doc:/a#section:A", "section",
                    "doc:/a#section:A", "section", null, null, null, null, "有界章节",
                    Map.of("truncated", true), List.of(), false);

            EvidenceResponse resp = run(List.of(bounded), ctx);
            assertThat(resp.evidence().get(0).truncated()).isTrue();
        }
    }

    @Nested
    @DisplayName("opaque refs")
    class OpaqueRefs {

        @Test
        @DisplayName("refs are ev_-prefixed, stable for the same key, and differ across keys")
        void stableAndDistinct() {
            EvidenceResponse resp = run(List.of(
                    evidence("s1", "doc:/a#seg:1", "segment", "doc:/a#seg:1", "prose", null,
                            1, 1, 1, "a", Map.of(), List.of(), false),
                    evidence("s1", "doc:/b#seg:1", "segment", "doc:/b#seg:1", "prose", null,
                            1, 1, 1, "b", Map.of(), List.of(), false)), ctx);

            String refA1 = resp.evidence().get(0).ref();
            String refB = resp.evidence().get(1).ref();
            assertThat(refA1).startsWith("ev_").hasSize("ev_".length() + 12);
            assertThat(refB).startsWith("ev_").isNotEqualTo(refA1);

            // 同输入再执行一次 → 稳定
            EvidenceResponse again = run(List.of(
                    evidence("s1", "doc:/a#seg:1", "segment", "doc:/a#seg:1", "prose", null,
                            1, 1, 1, "a", Map.of(), List.of(), false)), ctx);
            assertThat(again.evidence().get(0).ref()).isEqualTo(refA1);
        }

        @Test
        @DisplayName("refs are not enumerable: a different secret yields different refs")
        void notEnumerableAcrossSecrets() {
            EvidenceResponse resp = run(List.of(
                    evidence("s1", "doc:/a#seg:1", "segment", "doc:/a#seg:1", "prose", null,
                            1, 1, 1, "a", Map.of(), List.of(), false)), ctx);
            String refThisSecret = resp.evidence().get(0).ref();

            Object other = new AssembleOperator(EvidenceRefCodec.forSecret("other-secret"))
                    .execute(slotWith(evidence("s1", "doc:/a#seg:1", "segment", "doc:/a#seg:1",
                            "prose", null, 1, 1, 1, "a", Map.of(), List.of(), false)),
                            Params.empty(), ctx)
                    .get("evidenceResponse");
            assertThat(other).isInstanceOf(EvidenceResponse.class);
            assertThat(((EvidenceResponse) other).evidence().get(0).ref()).isNotEqualTo(refThisSecret);
        }

        @Test
        @DisplayName("assemble builds a request-scoped ref index for the R8 resolver")
        void refIndexCached() {
            EvidenceResponse resp = run(List.of(
                    evidence("s1", "doc:/a#seg:1", "segment", "doc:/a#seg:1", "prose", null,
                            1, 1, 1, "a", Map.of(), List.of(), false)), ctx);

            Object index = ctx.attributes().get(AssembleOperator.REF_INDEX_ATTRIBUTE);
            assertThat(index).isInstanceOf(Map.class);
            @SuppressWarnings("unchecked")
            Map<String, EvidenceRefResolver.ResolvedEvidence> map = (Map<String, EvidenceRefResolver.ResolvedEvidence>) index;
            assertThat(map).containsKey(resp.evidence().get(0).ref());
            assertThat(map.get(resp.evidence().get(0).ref()).canonicalEvidenceId())
                    .isEqualTo("doc:/a#seg:1");
        }

        private SlotValues slotWith(HydratedEvidence e) {
            SlotValues v = new SlotValues();
            v.put("hydratedEvidence", List.of(e));
            return v;
        }
    }

    @Nested
    @DisplayName("alias isolation")
    class AliasIsolation {

        @Test
        @DisplayName("alias question text never appears as evidence content (hydrate already resolved it)")
        void aliasTextAbsent() {
            // 模拟 alias 命中已被 hydrate 回源：canonical 是源证据，文本是原文而非别名问题
            String aliasQuestion = "最大功耗是多少？";
            HydratedEvidence resolved = evidence("s1", "doc:/spec#table_row:tbl:1:5", "table_row",
                    "doc:/spec#table_row:tbl:1:5", "table_row", "tbl:1", null, null, null,
                    "型号=X01 | 最大功耗=80W", Map.of(), List.of(), true);

            EvidenceResponse resp = run(List.of(resolved), ctx);
            assertThat(resp.evidence()).hasSize(1);
            assertThat(resp.evidence().get(0).content()).doesNotContain(aliasQuestion);
            assertThat(resp.evidence().get(0).content()).contains("最大功耗=80W");
        }
    }

    @Nested
    @DisplayName("empty inputs")
    class Empty {

        @Test
        @DisplayName("no hydrated evidence yields an empty, well-formed response")
        void emptyInput() {
            EvidenceResponse resp = run(List.of(), ctx);
            assertThat(resp.query()).isEqualTo("测试查询");
            assertThat(resp.evidence()).isEmpty();
            assertThat(resp.hasMore()).isFalse();
        }

        @Test
        @DisplayName("a context with no query falls back to the empty string")
        void nullQueryContext() {
            ExecContext noQuery = new ExecContext("req-2", "generic", "default", false);
            EvidenceResponse resp = run(List.of(), noQuery);
            assertThat(resp.query()).isEmpty();
        }
    }
}
