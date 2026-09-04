package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.result.EvidenceDocumentRow;
import com.coremasterkb.serving.mapper.result.SegmentTextRow;
import com.coremasterkb.serving.operator.mapper.EvidenceSourceV2Mapper;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import com.coremasterkb.serving.operator.operators.output.EvidenceHydrateOperator;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * A0-3（34 号 §P0-1 验收）：get_document 完整文档分页——空 cursor 含 segment 0、
 * 稀疏编号多页不漏不重、非法游标稳定错误、outline 仅首页。
 *
 * <p>mock 的 selectSegmentsPage 按 SQL 真实语义 {@code ordinal > afterIndex LIMIT n}
 * 模拟——测试钉住的是「传给 SQL 的排他下界」与「返回游标记录的实际位置」。</p>
 */
@DisplayName("A0-3 get_document 分页")
class EvidenceToolServiceDocumentPageTest {

    private static final String SNAP = "snap-1";

    private StructureRefService refService;
    private StructureToolMapper toolMapper;
    private EvidenceToolService service;

    @BeforeEach
    void setUp() {
        EvidenceRefCodec codec = EvidenceRefCodec.forSecret("test-secret");
        refService = mock(StructureRefService.class);
        toolMapper = mock(StructureToolMapper.class);
        EvidenceSourceV2Mapper sourceMapper = mock(EvidenceSourceV2Mapper.class);
        EvidenceHydrateOperator hydrate = mock(EvidenceHydrateOperator.class);
        service = new EvidenceToolService(refService, sourceMapper, toolMapper, hydrate, codec);

        when(refService.resolve(anyString(), anyString(), anyList(), anyString()))
                .thenReturn(new EvidenceRefResolver.ResolvedRef(
                        SNAP, EvidenceRefResolver.RefKind.DOCUMENT, "doc:/spec"));
        EvidenceDocumentRow doc = new EvidenceDocumentRow();
        doc.setSnapshotId(SNAP);
        doc.setDocumentName("spec.md");
        doc.setKbName("规范库");
        when(sourceMapper.selectDocumentSources(anyList())).thenReturn(List.of(doc));
        when(toolMapper.selectSectionOutline(eq(SNAP), anyInt())).thenReturn(List.of());
    }

    /** 模拟 SQL：ordinal > afterIndex 的前 n 行。 */
    private void stubSegments(int... ordinals) {
        List<SegmentTextRow> all = new ArrayList<>();
        for (int o : ordinals) {
            SegmentTextRow r = new SegmentTextRow();
            r.setSnapshotId(SNAP);
            r.setRef("doc:/spec#seg:" + o);
            r.setOrdinal(o);
            r.setBlockType("paragraph");
            r.setRawText("seg-" + o);
            r.setTokenCount(5);
            all.add(r);
        }
        when(toolMapper.countSegments(SNAP)).thenReturn(all.size());
        when(toolMapper.selectSegmentsPage(eq(SNAP), anyInt(), anyInt()))
                .thenAnswer(inv -> {
                    int after = inv.getArgument(1);
                    int limit = inv.getArgument(2);
                    return all.stream()
                            .filter(r -> r.getOrdinal() != null && r.getOrdinal() > after)
                            .limit(limit)
                            .toList();
                });
    }

    @Test
    @DisplayName("空 cursor：第一页包含 segment 0（不再漏首段）")
    void firstPageIncludesSegmentZero() {
        stubSegments(0, 1, 2, 3);

        var out = service.getDocument("doc_x", null, null, "odn", List.of("kb-1"), "alice");

        assertThat(out.segments()).extracting(EvidenceToolService.SegmentView::ordinal)
                .containsExactly(0, 1, 2, 3);
        assertThat(out.has_more()).isFalse();
        assertThat(out.cursor()).isNull();
    }

    @Test
    @DisplayName("连续编号多页：页间不漏不重，cursor 记录实际最后 ordinal")
    void contiguousPagination() {
        int[] ordinals = new int[201];
        for (int i = 0; i < 201; i++) {
            ordinals[i] = i;
        }
        stubSegments(ordinals);

        var page1 = service.getDocument("doc_x", 100, null, "odn", List.of("kb-1"), "alice");
        assertThat(page1.segments()).hasSize(100);
        assertThat(page1.segments().get(0).ordinal()).isEqualTo(0);
        assertThat(page1.has_more()).isTrue();
        assertThat(page1.cursor()).isNotNull();

        var page2 = service.getDocument("doc_x", 100, page1.cursor(),
                "odn", List.of("kb-1"), "alice");
        assertThat(page2.segments()).extracting(EvidenceToolService.SegmentView::ordinal)
                .hasSize(100)
                .startsWith(100)
                .endsWith(199);
        assertThat(page2.has_more()).isTrue();

        var page3 = service.getDocument("doc_x", 100, page2.cursor(),
                "odn", List.of("kb-1"), "alice");
        assertThat(page3.segments()).extracting(EvidenceToolService.SegmentView::ordinal)
                .containsExactly(200);
        assertThat(page3.has_more()).isFalse();
        assertThat(page3.cursor()).isNull();
    }

    @Test
    @DisplayName("稀疏编号多页：按实际 ordinal 推进，跳号不漏行")
    void sparseOrdinalsNoGaps() {
        stubSegments(0, 5, 9, 20, 21);

        var page1 = service.getDocument("doc_x", 2, null, "odn", List.of("kb-1"), "alice");
        assertThat(page1.segments()).extracting(EvidenceToolService.SegmentView::ordinal)
                .containsExactly(0, 5);
        assertThat(page1.has_more()).isTrue();

        var page2 = service.getDocument("doc_x", 2, page1.cursor(),
                "odn", List.of("kb-1"), "alice");
        assertThat(page2.segments()).extracting(EvidenceToolService.SegmentView::ordinal)
                .containsExactly(9, 20);
        assertThat(page2.has_more()).isTrue();

        var page3 = service.getDocument("doc_x", 2, page2.cursor(),
                "odn", List.of("kb-1"), "alice");
        assertThat(page3.segments()).extracting(EvidenceToolService.SegmentView::ordinal)
                .containsExactly(21);
        assertThat(page3.has_more()).isFalse();
    }

    @Test
    @DisplayName("空文档与单条文档：0 条空页、1 条含 segment 0")
    void emptyAndSingleSegment() {
        stubSegments();
        var empty = service.getDocument("doc_x", null, null, "odn", List.of("kb-1"), "alice");
        assertThat(empty.segments()).isEmpty();
        assertThat(empty.has_more()).isFalse();

        stubSegments(0);
        var single = service.getDocument("doc_x", null, null, "odn", List.of("kb-1"), "alice");
        assertThat(single.segments()).extracting(EvidenceToolService.SegmentView::ordinal)
                .containsExactly(0);
    }

    @Test
    @DisplayName("非法 cursor：稳定 typed error（不静默从头开始）")
    void invalidCursorRejected() {
        stubSegments(0, 1, 2);
        assertThatThrownBy(() -> service.getDocument("doc_x", null, "garbage!!!",
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .hasMessageContaining("cursor");
    }

    @Test
    @DisplayName("历史 o: 游标兼容：值按排他下界解释，o:0 = 从头")
    void legacyCursorCompatible() {
        stubSegments(0, 1, 2, 3, 4);
        String legacyOffset100 = java.util.Base64.getUrlEncoder().withoutPadding()
                .encodeToString("o:2".getBytes(java.nio.charset.StandardCharsets.UTF_8));
        var out = service.getDocument("doc_x", null, legacyOffset100,
                "odn", List.of("kb-1"), "alice");
        assertThat(out.segments()).extracting(EvidenceToolService.SegmentView::ordinal)
                .containsExactly(3, 4);

        String legacyZero = java.util.Base64.getUrlEncoder().withoutPadding()
                .encodeToString("o:0".getBytes(java.nio.charset.StandardCharsets.UTF_8));
        var fromStart = service.getDocument("doc_x", null, legacyZero,
                "odn", List.of("kb-1"), "alice");
        assertThat(fromStart.segments()).extracting(EvidenceToolService.SegmentView::ordinal)
                .startsWith(0);
    }
}
