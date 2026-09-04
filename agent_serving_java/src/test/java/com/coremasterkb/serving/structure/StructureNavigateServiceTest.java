package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.result.EvidenceDocumentRow;
import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.operator.mapper.EvidenceSourceV2Mapper;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

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
 * 批次8 R7 structure_navigate 契约（25 号 §6.10）：白名单关系、depth/limit 上限、
 * cursor 往返、st_ 公开投影、非法关系 typed error。
 */
@DisplayName("StructureNavigateService")
class StructureNavigateServiceTest {

    private static final String SNAP = "snap-1";
    private static final String SECTION = "doc:/spec#section:一/概述";
    private static final String SEG = "doc:/spec#seg:3";

    private EvidenceRefCodec codec;
    private StructureRefService refService;
    private StructureToolMapper toolMapper;
    private EvidenceSourceV2Mapper sourceMapper;
    private StructureNavigateService service;

    @BeforeEach
    void setUp() {
        codec = EvidenceRefCodec.forSecret("test-secret");
        refService = mock(StructureRefService.class);
        toolMapper = mock(StructureToolMapper.class);
        sourceMapper = mock(EvidenceSourceV2Mapper.class);
        service = new StructureNavigateService(refService, toolMapper, sourceMapper, codec);

        EvidenceDocumentRow doc = new EvidenceDocumentRow();
        doc.setSnapshotId(SNAP);
        doc.setDocumentName("spec.md");
        doc.setKbName("规范库");
        when(sourceMapper.selectDocumentSources(anyList())).thenReturn(List.of(doc));
    }

    private String stRef(String internal) {
        when(refService.resolve(anyString(), anyString(), anyList(), anyString()))
                .thenReturn(new EvidenceRefResolver.ResolvedRef(
                        SNAP, EvidenceRefResolver.RefKind.STRUCTURE, internal));
        return codec.encodeStructure(SNAP, internal);
    }

    @Test
    @DisplayName("children：直接子节点 + st_ 公开 ref + 可继续关系")
    void childrenRelation() {
        String ref = stRef(SECTION);
        when(toolMapper.selectNode(SNAP, SECTION)).thenReturn(node("section", SECTION, null, 1));
        when(toolMapper.selectChildren(eq(SNAP), eq(SECTION), anyInt(), anyInt()))
                .thenReturn(List.of(node("segment", SEG, SECTION, 3), node("segment", "doc:/spec#seg:4", SECTION, 4)));

        var out = service.navigate(ref, "children", null, null, null, "odn", List.of("kb-1"), "alice");

        assertThat(out.nodes()).hasSize(2);
        assertThat(out.nodes().get(0).ref()).isEqualTo(codec.encodeStructure(SNAP, SEG));
        assertThat(out.nodes().get(0).node_type()).isEqualTo("segment");
        assertThat(out.nodes().get(0).ordinal()).isEqualTo(3);
        assertThat(out.nodes().get(0).relations()).contains("previous", "next", "parent");
        assertThat(out.has_more()).isFalse();
        assertThat(out.source()).containsEntry("file_name", "spec.md");
    }

    @Test
    @DisplayName("children 超限 → 截断 + has_more + cursor（offset 稳定续页）")
    void childrenPagination() {
        String ref = stRef(SECTION);
        when(toolMapper.selectNode(SNAP, SECTION)).thenReturn(node("section", SECTION, null, 1));
        List<StructureNodeRow> many = new java.util.ArrayList<>();
        for (int i = 0; i < 6; i++) {
            many.add(node("segment", SEG + i, SECTION, i));
        }
        when(toolMapper.selectChildren(eq(SNAP), eq(SECTION), anyInt(), anyInt())).thenReturn(many);

        var page1 = service.navigate(ref, "children", null, 5, null, "odn", List.of("kb-1"), "alice");
        assertThat(page1.nodes()).hasSize(5);
        assertThat(page1.has_more()).isTrue();
        assertThat(page1.cursor()).isNotBlank();

        var page2 = service.navigate(ref, "children", null, 5, page1.cursor(),
                "odn", List.of("kb-1"), "alice");
        // 第二页从 offset=5 继续（mock 全量返回 → 截 5 条，has_more 由 size>limit 判定）
        assertThat(page2.nodes()).hasSize(5);
    }

    @Test
    @DisplayName("parent：返回唯一父节点摘要")
    void parentRelation() {
        String ref = stRef(SEG);
        when(toolMapper.selectNode(SNAP, SEG)).thenReturn(node("segment", SEG, SECTION, 3));
        when(toolMapper.selectNode(SNAP, SECTION)).thenReturn(node("section", SECTION, null, 1));

        var out = service.navigate(ref, "parent", null, null, null, "odn", List.of("kb-1"), "alice");

        assertThat(out.nodes()).hasSize(1);
        assertThat(out.nodes().get(0).ref()).isEqualTo(codec.encodeStructure(SNAP, SECTION));
        assertThat(out.nodes().get(0).node_type()).isEqualTo("section");
    }

    @Test
    @DisplayName("previous/next：同父兄弟按 ordinal 相邻")
    void adjacentSiblings() {
        String ref = stRef(SEG);
        when(toolMapper.selectNode(SNAP, SEG)).thenReturn(node("segment", SEG, SECTION, 3));
        when(toolMapper.selectSiblings(eq(SNAP), eq(SECTION), anyInt()))
                .thenReturn(List.of(node("segment", "doc:/spec#seg:2", SECTION, 2),
                        node("segment", SEG, SECTION, 3),
                        node("segment", "doc:/spec#seg:4", SECTION, 4)));

        var next = service.navigate(ref, "next", null, null, null, "odn", List.of("kb-1"), "alice");
        assertThat(next.nodes()).hasSize(1);
        assertThat(next.nodes().get(0).ordinal()).isEqualTo(4);

        var prev = service.navigate(ref, "previous", null, null, null, "odn", List.of("kb-1"), "alice");
        assertThat(prev.nodes()).hasSize(1);
        assertThat(prev.nodes().get(0).ordinal()).isEqualTo(2);
    }

    @Test
    @DisplayName("container：segment → 最近 section 祖先")
    void containerOfSegment() {
        String ref = stRef(SEG);
        when(toolMapper.selectNode(SNAP, SEG)).thenReturn(node("segment", SEG, SECTION, 3));
        when(toolMapper.selectAncestors(SNAP, SEG, StructureNavigateService.MAX_DEPTH))
                .thenReturn(List.of(node("section", SECTION, "doc:/spec", null)));

        var out = service.navigate(ref, "container", null, null, null, "odn", List.of("kb-1"), "alice");
        assertThat(out.nodes()).hasSize(1);
        assertThat(out.nodes().get(0).node_type()).isEqualTo("section");
    }

    @Test
    @DisplayName("references：仅跟随显式边（无边 = 空结果，正常）")
    void referencesFollowExplicitEdgesOnly() {
        String ref = stRef(SEG);
        when(toolMapper.selectNode(SNAP, SEG)).thenReturn(node("segment", SEG, SECTION, 3));
        when(toolMapper.selectEdges(SNAP, SEG, "reference", StructureNavigateService.DEFAULT_LIMIT))
                .thenReturn(List.of());

        var out = service.navigate(ref, "references", null, null, null, "odn", List.of("kb-1"), "alice");
        assertThat(out.nodes()).isEmpty();
        assertThat(out.has_more()).isFalse();
    }

    @Test
    @DisplayName("caption：table 节点取 structural_context；非 table = 空")
    void captionOfTable() {
        String tableRef = "doc:/spec#table:tbl:3";
        String ref = stRef(tableRef);
        when(toolMapper.selectNode(SNAP, tableRef)).thenReturn(node("table", tableRef, SECTION, null));
        when(toolMapper.selectStructuralContext(SNAP, "tbl:3")).thenReturn("表 3 设备功耗清单");

        var out = service.navigate(ref, "caption", null, null, null, "odn", List.of("kb-1"), "alice");
        assertThat(out.nodes()).hasSize(1);
        assertThat(out.nodes().get(0).node_type()).isEqualTo("caption");
        assertThat(out.nodes().get(0).title()).isEqualTo("表 3 设备功耗清单");
    }

    @Test
    @DisplayName("非法关系 → unsupported_operation(400)，details 带白名单")
    void unknownRelationRejected() {
        String ref = stRef(SEG);
        assertThatThrownBy(() -> service.navigate(ref, "siblings", null, null, null,
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .satisfies(e -> {
                    StructureToolException ste = (StructureToolException) e;
                    assertThat(ste.code()).isEqualTo("unsupported_operation");
                    assertThat(ste.details()).containsKey("allowed_relations");
                });
    }

    @Test
    @DisplayName("depth/limit 上限 clamp（descendants depth≤3, limit≤200）")
    void depthAndLimitClamped() {
        String ref = stRef(SECTION);
        when(toolMapper.selectNode(SNAP, SECTION)).thenReturn(node("section", SECTION, null, 1));
        when(toolMapper.selectDescendants(eq(SNAP), eq(SECTION), eq(3), anyInt(), eq(0)))
                .thenReturn(List.of());

        service.navigate(ref, "descendants", 99, 5000, null, "odn", List.of("kb-1"), "alice");
        org.mockito.Mockito.verify(toolMapper)
                .selectDescendants(eq(SNAP), eq(SECTION), eq(3), eq(201), eq(0));
    }

    @Test
    @DisplayName("A0-2: children({doc}#document) 历史快照（节点与 parent 均裸 ref）返回顶层章节")
    void childrenOfDocumentRefOnLegacySnapshot() {
        String ref = stRef("doc:/spec#document");
        // 历史快照：#document 精确 miss，document 节点行是裸 ref，section parent 也是裸 ref
        when(toolMapper.selectNode(SNAP, "doc:/spec#document")).thenReturn(null);
        when(toolMapper.selectNode(SNAP, "doc:/spec"))
                .thenReturn(node("document", "doc:/spec", null, null));
        when(toolMapper.selectChildren(eq(SNAP), eq("doc:/spec"), anyInt(), anyInt()))
                .thenReturn(List.of(node("section", SECTION, "doc:/spec", 1)));

        var out = service.navigate(ref, "children", null, null, null,
                "odn", List.of("kb-1"), "alice");

        assertThat(out.nodes()).hasSize(1);
        assertThat(out.nodes().get(0).node_type()).isEqualTo("section");
    }

    @Test
    @DisplayName("A0-2: descendants({doc}#document) 新快照直接按 #document ref 展开")
    void descendantsOfDocumentRefOnNewSnapshot() {
        String ref = stRef("doc:/spec#document");
        when(toolMapper.selectNode(SNAP, "doc:/spec#document"))
                .thenReturn(node("document", "doc:/spec#document", null, null));
        when(toolMapper.selectDescendants(eq(SNAP), eq("doc:/spec#document"), anyInt(), anyInt(), anyInt()))
                .thenReturn(List.of(node("section", SECTION, "doc:/spec#document", 1)));

        var out = service.navigate(ref, "descendants", null, null, null,
                "odn", List.of("kb-1"), "alice");

        assertThat(out.nodes()).hasSize(1);
        assertThat(out.nodes().get(0).node_type()).isEqualTo("section");
    }

    @Test
    @DisplayName("cursor 往返：encode → decode 稳定；非法 cursor → typed error")
    void cursorRoundTrip() {
        String token = Cursors.encodeOffset(120);
        assertThat(Cursors.decodeOffset(token)).isEqualTo(120);
        assertThat(Cursors.decodeOffset(null)).isZero();
        assertThat(Cursors.decodeOffset("")).isZero();

        assertThatThrownBy(() -> Cursors.decodeOffset("not-a-cursor!!"))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> ((StructureToolException) e).code())
                .isEqualTo("unsupported_operation");
    }

    private static StructureNodeRow node(String type, String ref, String parent, Integer ordinal) {
        StructureNodeRow n = new StructureNodeRow();
        n.setSnapshotId(SNAP);
        n.setNodeType(type);
        n.setRef(ref);
        n.setParentRef(parent);
        n.setOrdinal(ordinal);
        if ("section".equals(type)) {
            n.setTitle(ref.substring(ref.lastIndexOf(':') + 1));
            n.setLevel(1);
        }
        if ("segment".equals(type)) {
            n.setBlockType("paragraph");
        }
        return n;
    }
}
