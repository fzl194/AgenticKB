package com.coremasterkb.serving.operator.operators.output;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.HydratedEvidence;
import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.mapper.result.EvidenceDocumentRow;
import com.coremasterkb.serving.mapper.result.SegmentTextRow;
import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.mapper.result.TableAssetRow;
import com.coremasterkb.serving.mapper.result.TableCellRow;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotValues;
import com.coremasterkb.serving.operator.mapper.EvidenceSourceV2Mapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.*;

/**
 * 批次8 R5：{@code evidence_hydrate} 类型化展开矩阵（prose 邻窗/parent、table_row 表头回填、
 * alias 回源、失败跳过、系统失败上抛）+ 批量分组查询断言（每 mapper 方法恰好一次 = 禁 N+1）。
 */
@DisplayName("EvidenceHydrateOperator")
class EvidenceHydrateOperatorTest {

    private static final String SNAP = "snap-1";
    private static final ActiveScope SCOPE =
            new ActiveScope("rel", null, List.of(SNAP), Map.of(), Map.of());

    private EvidenceSourceV2Mapper mapper;
    private EvidenceHydrateOperator op;
    private ExecContext ctx;

    @BeforeEach
    void setUp() {
        mapper = mock(EvidenceSourceV2Mapper.class);
        op = new EvidenceHydrateOperator(mapper);
        ctx = new ExecContext("req-1", "generic", "default", false);
        ctx.setQuery("设备最大功耗");

        // 公共 source projection
        EvidenceDocumentRow doc = new EvidenceDocumentRow();
        doc.setSnapshotId(SNAP);
        doc.setDocumentKey("doc:/spec");
        doc.setDocumentName("spec.md");
        doc.setKbId("kb-1");
        doc.setKbName("规范库");
        doc.setRelativePath("规范/spec.md");
        when(mapper.selectDocumentSources(anyList())).thenReturn(List.of(doc));
        when(mapper.selectDocumentTokenTotals(anyList())).thenReturn(List.of());
    }

    // ---------------------------------------------------------------- fixture factories

    private static RetrievalCandidate candidate(String snapshot, String canonical, String targetType,
                                                String targetRef, String repType) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("snapshot_id", snapshot);
        return new RetrievalCandidate("rep-" + canonical, 1.0, "fts", metadata, null,
                List.of("rep-" + canonical), repType, canonical, targetType, targetRef,
                "fts", 1, 1.0, "ranking text", Map.of());
    }

    private static UnitV2Row rep(String canonical, String repType, String content,
                                 String structuralContext, String facetsJson, String containerRef) {
        UnitV2Row row = new UnitV2Row();
        row.setRepresentationId("rep-" + canonical);
        row.setSnapshotId(SNAP);
        row.setRepresentationType(repType);
        row.setContentType(repType);
        row.setContentText(content);
        row.setStructuralContext(structuralContext == null ? "" : structuralContext);
        row.setTargetType("segment");
        row.setTargetRef(canonical);
        row.setCanonicalEvidenceId(canonical);
        row.setContainerRef(containerRef);
        row.setOrdinal(5);
        row.setFacetsJson(facetsJson == null ? "{}" : facetsJson);
        return row;
    }

    private static StructureNodeRow node(String ref, String parentRef, int ordinal, String blockType) {
        StructureNodeRow n = new StructureNodeRow();
        n.setSnapshotId(SNAP);
        n.setNodeType("segment");
        n.setRef(ref);
        n.setParentRef(parentRef);
        n.setOrdinal(ordinal);
        n.setBlockType(blockType);
        return n;
    }

    private static SegmentTextRow seg(String parentRef, int ordinal, String text, Integer tokens) {
        SegmentTextRow r = new SegmentTextRow();
        r.setSnapshotId(SNAP);
        r.setRef("doc:/spec#seg:" + ordinal);
        r.setParentRef(parentRef);
        r.setOrdinal(ordinal);
        r.setBlockType("paragraph");
        r.setRawText(text);
        r.setTokenCount(tokens);
        r.setHeadingChainJson("[]");
        return r;
    }

    private SlotValues run(RetrievalCandidate... candidates) {
        SlotValues in = new SlotValues();
        in.put("candidates", List.of(candidates));
        in.put("scope", SCOPE);
        return op.execute(in, Params.empty(), ctx);
    }

    @SuppressWarnings("unchecked")
    private static List<HydratedEvidence> result(SlotValues out) {
        return (List<HydratedEvidence>) out.get("hydratedEvidence");
    }

    // ---------------------------------------------------------------- typed expansion matrix

    @Nested
    @DisplayName("prose / segment expansion")
    class ProseExpansion {

        private static final String SECTION = "doc:/spec#section:设备规格";
        private static final String CANONICAL = "doc:/spec#seg:5";

        private void stubCommon() {
            when(mapper.selectCanonicalRepresentations(anyList(), anyList())).thenReturn(List.of(
                    rep(CANONICAL, "prose", "命中的原文段落", "",
                            "{\"section_path\":\"第二章/设备规格\",\"document\":\"doc:/spec\"}", null)));
            when(mapper.selectStructureNodes(anyList(), anyList())).thenReturn(List.of(
                    node(CANONICAL, SECTION, 5, "paragraph")));
        }

        @Test
        @DisplayName("auto + parent fits → parent mode (section children aggregated)")
        void autoParentWhenFits() {
            stubCommon();
            when(mapper.selectWindowSegments(anyList())).thenReturn(List.of(
                    seg(SECTION, 4, "前文", 10), seg(SECTION, 5, "命中的原文段落", 10),
                    seg(SECTION, 6, "后文", 10)));
            when(mapper.selectSectionSegments(anyList(), anyList(), anyInt())).thenReturn(List.of(
                    seg(SECTION, 3, "章节开头", 10), seg(SECTION, 4, "前文", 10),
                    seg(SECTION, 5, "命中的原文段落", 10), seg(SECTION, 6, "后文", 10),
                    seg(SECTION, 7, "章节结尾", 10)));

            List<HydratedEvidence> out = result(run(candidate(SNAP, CANONICAL, "segment",
                    CANONICAL, "prose")));

            assertThat(out).hasSize(1);
            HydratedEvidence e = out.get(0);
            assertThat(e.expansionMode()).isEqualTo("parent");
            assertThat(e.evidenceType()).isEqualTo("prose");
            assertThat(e.contentText()).contains("章节开头").contains("章节结尾");
            assertThat(e.orderedFragments()).allSatisfy(f -> assertThat(f.kind()).isEqualTo("section"));
            assertThat(e.parentRef()).isEqualTo(SECTION);
            assertThat(e.windowFrom()).isEqualTo(3);
            assertThat(e.windowTo()).isEqualTo(7);
            assertThat(e.source().knowledgeBase()).isEqualTo("规范库");
            assertThat(e.source().fileName()).isEqualTo("spec.md");
            assertThat(e.source().section()).isEqualTo("第二章/设备规格");
        }

        @Test
        @DisplayName("auto + parent too large → bounded window (exact hit + neighbors)")
        void autoWindowWhenParentTooLarge() {
            stubCommon();
            when(mapper.selectWindowSegments(anyList())).thenReturn(List.of(
                    seg(SECTION, 4, "前文", 10), seg(SECTION, 5, "命中的原文段落", 10),
                    seg(SECTION, 6, "后文", 10)));
            when(mapper.selectSectionSegments(anyList(), anyList(), anyInt())).thenReturn(List.of(
                    seg(SECTION, 3, "超大章节开头", 4000), seg(SECTION, 4, "前文", 4000),
                    seg(SECTION, 5, "命中的原文段落", 4000), seg(SECTION, 6, "后文", 4000)));

            List<HydratedEvidence> out = result(run(candidate(SNAP, CANONICAL, "segment",
                    CANONICAL, "prose")));

            assertThat(out).hasSize(1);
            HydratedEvidence e = out.get(0);
            assertThat(e.expansionMode()).isEqualTo("window");
            assertThat(e.contentText()).isEqualTo("前文\n命中的原文段落\n后文");
            // 命中行 kind=exact，邻行 kind=window
            assertThat(e.orderedFragments()).extracting(HydratedEvidence.EvidenceFragment::kind)
                    .containsExactly("window", "exact", "window");
        }

        @Test
        @DisplayName("mode=exact → only the source representation text")
        void exactMode() {
            stubCommon();
            SlotValues in = new SlotValues();
            in.put("candidates", List.of(candidate(SNAP, CANONICAL, "segment", CANONICAL, "prose")));
            in.put("scope", SCOPE);
            SlotValues out = op.execute(in,
                    new Params(new com.fasterxml.jackson.databind.ObjectMapper()
                            .createObjectNode().put("mode", "exact")), ctx);

            List<HydratedEvidence> list = result(out);
            assertThat(list).hasSize(1);
            assertThat(list.get(0).expansionMode()).isEqualTo("exact");
            assertThat(list.get(0).contentText()).isEqualTo("命中的原文段落");
            assertThat(list.get(0).orderedFragments()).hasSize(1);
        }
    }

    @Nested
    @DisplayName("table_row expansion")
    class TableRowExpansion {

        @Test
        @DisplayName("caption + header backfill from columns_json + hit row values")
        void headerAndRowBackfilled() {
            String canonical = "doc:/spec#table_row:tbl:1:5";
            when(mapper.selectCanonicalRepresentations(anyList(), anyList())).thenReturn(List.of(
                    rep(canonical, "table_row", "型号为X01；最大功耗为80W",
                            "设备清单 | 表头: 型号/最大功耗",
                            "{\"document\":\"doc:/spec\"}", "tbl:1")));
            TableAssetRow asset = new TableAssetRow();
            asset.setSnapshotId(SNAP);
            asset.setAssetRef("doc:/spec#table:tbl:1");
            asset.setAssetType("table");
            asset.setTableRef("tbl:1");
            asset.setColumnsJson("[\"型号\",\"最大功耗\"]");
            asset.setRowCount(6);
            asset.setReadiness("ready");
            asset.setSchemaVersion("asset-v2-1");
            when(mapper.selectTableAssets(anyList(), anyList())).thenReturn(List.of(asset));

            TableCellRow c1 = new TableCellRow();
            c1.setSnapshotId(SNAP);
            c1.setTableRef("tbl:1");
            c1.setRowIndex(5);
            c1.setColumnIndex(0);
            c1.setColumnName("型号");
            c1.setValue("X01");
            c1.setIsHeader(false);
            TableCellRow c2 = new TableCellRow();
            c2.setSnapshotId(SNAP);
            c2.setTableRef("tbl:1");
            c2.setRowIndex(5);
            c2.setColumnIndex(1);
            c2.setColumnName("最大功耗");
            c2.setValue("80W");
            c2.setIsHeader(false);
            when(mapper.selectTableCells(anyList(), anyList(), anyInt()))
                    .thenReturn(List.of(c1, c2));

            List<HydratedEvidence> out = result(run(candidate(SNAP, canonical, "table_row",
                    canonical, "table_row")));

            assertThat(out).hasSize(1);
            HydratedEvidence e = out.get(0);
            // 2026-09-01 行命中 → 整表视图：evidenceType 升 table（cells 在手直接
            // 重建整表；同表多条命中由 assemble 同 ref 互含去重）。命中行 ordinal
            // 仍在 provenance。
            assertThat(e.evidenceType()).isEqualTo("table");
            assertThat(e.orderedFragments()).extracting(HydratedEvidence.EvidenceFragment::kind)
                    .containsExactly("caption", "header", "row");
            assertThat(e.contentText()).contains("设备清单").contains("表头: 型号 / 最大功耗")
                    .contains("型号=X01").contains("最大功耗=80W");
            assertThat(e.structureRefs()).containsExactly("doc:/spec#table:tbl:1");
            assertThat(e.navigable()).isTrue();
            assertThat(e.provenance()).containsEntry("rowIndex", 5);
        }
    }

    @Nested
    @DisplayName("alias resolution & failure semantics")
    class AliasAndFailures {

        @Test
        @DisplayName("alias hit resolves back to the source evidence text, never the alias question")
        void aliasResolvesToSource() {
            String canonical = "doc:/spec#seg:5";
            String section = "doc:/spec#section:设备规格";
            // 批量查询只返回 returnable=TRUE 的源表示（生产 SQL 保证；此处模拟）
            when(mapper.selectCanonicalRepresentations(anyList(), anyList())).thenReturn(List.of(
                    rep(canonical, "prose", "源证据原文：最大功耗 80W", "", "{}", null)));
            when(mapper.selectStructureNodes(anyList(), anyList())).thenReturn(List.of(
                    node(canonical, section, 5, "paragraph")));
            when(mapper.selectWindowSegments(anyList())).thenReturn(List.of(
                    seg(section, 5, "源证据原文：最大功耗 80W", 10)));
            when(mapper.selectSectionSegments(anyList(), anyList(), anyInt()))
                    .thenReturn(List.of(seg(section, 5, "源证据原文：最大功耗 80W", 4000)));

            // 候选自 query_alias 命中（repType=query_alias），hydrate 后必须回源
            List<HydratedEvidence> out = result(run(candidate(SNAP, canonical, "segment",
                    canonical, "query_alias")));

            assertThat(out).hasSize(1);
            assertThat(out.get(0).contentText()).contains("源证据原文：最大功耗 80W");
            assertThat(out.get(0).contentText()).doesNotContain("最大功耗是多少");
            assertThat(out.get(0).provenance()).containsEntry("sourceRepresentationType", "prose");
        }

        @Test
        @DisplayName("unparseable target refs are skipped with trace; other candidates survive")
        void unparseableSkipped() {
            when(mapper.selectCanonicalRepresentations(anyList(), anyList())).thenReturn(List.of());
            when(mapper.selectStructureNodes(anyList(), anyList())).thenReturn(List.of());

            List<HydratedEvidence> out = result(run(
                    candidate(SNAP, "garbage-ref", "segment", "garbage-ref", "prose"),
                    candidate(SNAP, "doc:/spec#document", "document", "doc:/spec#document", "section")));

            // garbage 解析失败跳过；document 无 rep 也无法回源 → 全部跳过但留痕
            assertThat(out).isEmpty();
            Object skipped = ctx.attributes().get("hydrateSkipped");
            assertThat(skipped).isInstanceOf(List.class);
            assertThat((List<?>) skipped).hasSize(2);
            assertThat(((List<?>) skipped).get(0).toString()).contains("unparseable_target_ref");
        }

        @Test
        @DisplayName("snapshot outside the authorized scope is skipped")
        void outOfScopeSkipped() {
            List<HydratedEvidence> out = result(run(
                    candidate("snap-other", "doc:/spec#seg:5", "segment", "doc:/spec#seg:5", "prose")));

            assertThat(out).isEmpty();
            Object skipped = ctx.attributes().get("hydrateSkipped");
            assertThat(((List<?>) skipped).get(0).toString())
                    .contains("snapshot_out_of_scope_or_missing");
        }

        @Test
        @DisplayName("systemic storage failure propagates (never masked as empty)")
        void systemicFailurePropagates() {
            when(mapper.selectCanonicalRepresentations(anyList(), anyList()))
                    .thenThrow(new RuntimeException("db down"));

            SlotValues in = new SlotValues();
            in.put("candidates", List.of(candidate(SNAP, "doc:/spec#seg:5", "segment",
                    "doc:/spec#seg:5", "prose")));
            in.put("scope", SCOPE);

            assertThatThrownBy(() -> op.execute(in, Params.empty(), ctx))
                    .isInstanceOf(RuntimeException.class)
                    .hasMessageContaining("db down");
        }
    }

    @Nested
    @DisplayName("batch grouping (no N+1)")
    class BatchQueries {

        @Test
        @DisplayName("mixed candidate set issues each batch query exactly once")
        void eachBatchQueryOnce() {
            String sectionA = "doc:/spec#section:A";
            String sectionB = "doc:/spec#section:B";
            when(mapper.selectCanonicalRepresentations(anyList(), anyList())).thenReturn(List.of(
                    rep("doc:/spec#seg:1", "prose", "a1", "", "{}", null),
                    rep("doc:/spec#seg:2", "prose", "a2", "", "{}", null),
                    rep("doc:/spec#table_row:tbl:1:5", "table_row", "row", "caption", "{}", "tbl:1")));
            when(mapper.selectStructureNodes(anyList(), anyList())).thenReturn(List.of(
                    node("doc:/spec#seg:1", sectionA, 1, "paragraph"),
                    node("doc:/spec#seg:2", sectionB, 2, "paragraph")));
            when(mapper.selectWindowSegments(anyList())).thenReturn(List.of(
                    seg(sectionA, 1, "a1", 10)));
            when(mapper.selectSectionSegments(anyList(), anyList(), anyInt()))
                    .thenReturn(List.of(seg(sectionA, 1, "a1", 10)));
            when(mapper.selectTableAssets(anyList(), anyList())).thenReturn(List.of());
            when(mapper.selectTableCells(anyList(), anyList(), anyInt())).thenReturn(List.of());

            List<HydratedEvidence> out = result(run(
                    candidate(SNAP, "doc:/spec#seg:1", "segment", "doc:/spec#seg:1", "prose"),
                    candidate(SNAP, "doc:/spec#seg:2", "segment", "doc:/spec#seg:2", "prose"),
                    candidate(SNAP, "doc:/spec#table_row:tbl:1:5", "table_row",
                            "doc:/spec#table_row:tbl:1:5", "table_row")));
            assertThat(out).isNotEmpty();

            verify(mapper, times(1)).selectCanonicalRepresentations(anyList(), anyList());
            verify(mapper, times(1)).selectStructureNodes(anyList(), anyList());
            verify(mapper, times(1)).selectWindowSegments(anyList());
            verify(mapper, times(1)).selectSectionSegments(anyList(), anyList(), anyInt());
            verify(mapper, times(1)).selectTableAssets(anyList(), anyList());
            verify(mapper, times(1)).selectTableCells(anyList(), anyList(), anyInt());
            verify(mapper, times(1)).selectDocumentSources(anyList());
            verify(mapper, times(1)).selectDocumentTokenTotals(anyList());

            // 两个 prose 锚点在同一次窗口批量里
            ArgumentCaptor<List<EvidenceSourceV2Mapper.WindowAnchor>> anchors = ArgumentCaptor.forClass(List.class);
            verify(mapper).selectWindowSegments(anchors.capture());
            assertThat(anchors.getValue()).hasSize(2);
        }

        @Test
        @DisplayName("canonical duplicates hydrate once (keep-first)")
        void canonicalDuplicatesOnce() {
            String canonical = "doc:/spec#seg:1";
            String section = "doc:/spec#section:A";
            when(mapper.selectCanonicalRepresentations(anyList(), anyList())).thenReturn(List.of(
                    rep(canonical, "prose", "a1", "", "{}", null)));
            when(mapper.selectStructureNodes(anyList(), anyList())).thenReturn(List.of(
                    node(canonical, section, 1, "paragraph")));
            when(mapper.selectWindowSegments(anyList())).thenReturn(List.of(seg(section, 1, "a1", 10)));
            when(mapper.selectSectionSegments(anyList(), anyList(), anyInt()))
                    .thenReturn(List.of(seg(section, 1, "a1", 10)));

            List<HydratedEvidence> out = result(run(
                    candidate(SNAP, canonical, "segment", canonical, "prose"),
                    candidate(SNAP, canonical, "segment", canonical, "query_alias")));

            assertThat(out).hasSize(1);
        }
    }

    @Nested
    @DisplayName("declaration")
    class Declaration {

        @Test
        @DisplayName("registers as output-category operator with HYDRATED_EVIDENCE_LIST output")
        void definition() {
            var def = op.definition();
            assertThat(def.type()).isEqualTo("evidence_hydrate");
            assertThat(def.category()).isEqualTo("output");
            assertThat(def.outputSlots()).hasSize(1);
            assertThat(def.outputSlots().get(0).type())
                    .isEqualTo(com.coremasterkb.serving.operator.core.SlotType.HYDRATED_EVIDENCE_LIST);
        }

        @Test
        @DisplayName("empty candidates yield an empty list without touching the mapper")
        void emptyInputShortCircuits() {
            SlotValues out = run();
            assertThat(result(out)).isEmpty();
            verifyNoInteractions(mapper);
        }
    }
}
