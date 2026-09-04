package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * A0-2（34 号 §P0）：document 结构引用的双变体解析。
 *
 * <p>历史快照的 document 节点 ref 是裸 {@code {doc}}，新快照（mining 修复后）与
 * retrieval target_ref 一致为 {@code {doc}#document}。st_ 按任一身份编码都必须能
 * 解析到该快照里真实存在的 document 节点行——精确匹配 miss 时按变体回退，
 * 两个变体都不存在才 invalid（不默默跳到错误文档）。</p>
 */
@DisplayName("A0-2 StructureNodeLookup：document ref 变体解析")
class StructureNodeLookupTest {

    private static final String SNAP = "snap-1";
    private static final String DOC = "doc:/spec";

    @Test
    @DisplayName("documentVariants：#document ↔ 裸 ref 双向；非 document 形式单值")
    void variants() {
        assertThat(StructureNodeLookup.documentVariants("doc:/spec#document"))
                .containsExactly("doc:/spec#document", "doc:/spec");
        assertThat(StructureNodeLookup.documentVariants("doc:/spec"))
                .containsExactly("doc:/spec", "doc:/spec#document");
        // 非 document 形式（segment/section/table）只有自身——不做变体猜测
        assertThat(StructureNodeLookup.documentVariants("doc:/spec#seg:3"))
                .containsExactly("doc:/spec#seg:3");
        assertThat(StructureNodeLookup.documentVariants("doc:/spec#section:一"))
                .containsExactly("doc:/spec#section:一");
        assertThat(StructureNodeLookup.documentVariants("doc:/spec#table:tbl:1"))
                .containsExactly("doc:/spec#table:tbl:1");
        assertThat(StructureNodeLookup.documentVariants(null)).isEmpty();
        assertThat(StructureNodeLookup.documentVariants("")).isEmpty();
    }

    @Test
    @DisplayName("精确命中优先，不做多余变体查询")
    void exactMatchFirst() {
        StructureToolMapper mapper = mock(StructureToolMapper.class);
        StructureNodeRow hit = new StructureNodeRow();
        hit.setSnapshotId(SNAP);
        hit.setNodeType("document");
        hit.setRef(DOC + "#document");
        when(mapper.selectNode(SNAP, DOC + "#document")).thenReturn(hit);

        StructureNodeRow out = StructureNodeLookup.find(mapper, SNAP, DOC + "#document");

        assertThat(out).isSameAs(hit);
        verify(mapper, never()).selectNode(SNAP, DOC);
    }

    @Test
    @DisplayName("历史快照：#document 精确 miss → 裸 ref 变体回退命中")
    void legacyBareRefFallback() {
        StructureToolMapper mapper = mock(StructureToolMapper.class);
        StructureNodeRow legacy = new StructureNodeRow();
        legacy.setSnapshotId(SNAP);
        legacy.setNodeType("document");
        legacy.setRef(DOC);
        when(mapper.selectNode(SNAP, DOC + "#document")).thenReturn(null);
        when(mapper.selectNode(SNAP, DOC)).thenReturn(legacy);

        StructureNodeRow out = StructureNodeLookup.find(mapper, SNAP, DOC + "#document");

        assertThat(out).isSameAs(legacy);
    }

    @Test
    @DisplayName("裸 ref st_（历史 node 候选编码）在仅存 #document 行的新快照上回退命中")
    void newSnapshotReverseFallback() {
        StructureToolMapper mapper = mock(StructureToolMapper.class);
        StructureNodeRow fresh = new StructureNodeRow();
        fresh.setSnapshotId(SNAP);
        fresh.setNodeType("document");
        fresh.setRef(DOC + "#document");
        when(mapper.selectNode(eq(SNAP), eq(DOC))).thenReturn(null);
        when(mapper.selectNode(SNAP, DOC + "#document")).thenReturn(fresh);

        StructureNodeRow out = StructureNodeLookup.find(mapper, SNAP, DOC);

        assertThat(out).isSameAs(fresh);
    }

    @Test
    @DisplayName("两个变体都不存在 → null（调用方按 invalid_ref 拒绝，不猜其它文档）")
    void unresolvedReturnsNull() {
        StructureToolMapper mapper = mock(StructureToolMapper.class);
        when(mapper.selectNode(eq(SNAP), eq("doc:/other"))).thenReturn(null);
        when(mapper.selectNode(eq(SNAP), eq("doc:/other#document"))).thenReturn(null);

        assertThat(StructureNodeLookup.find(mapper, SNAP, "doc:/other#document")).isNull();
    }

    @Test
    @DisplayName("非 document ref miss 时只查一次（不产生变体查询）")
    void nonDocumentNoVariantQueries() {
        StructureToolMapper mapper = mock(StructureToolMapper.class);
        when(mapper.selectNode(SNAP, "doc:/spec#seg:3")).thenReturn(null);

        assertThat(StructureNodeLookup.find(mapper, SNAP, "doc:/spec#seg:3")).isNull();
        verify(mapper, org.mockito.Mockito.times(1))
                .selectNode(org.mockito.ArgumentMatchers.anyString(),
                        org.mockito.ArgumentMatchers.anyString());
    }
}
