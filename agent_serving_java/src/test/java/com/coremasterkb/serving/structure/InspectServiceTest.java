package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.mapper.result.TableAssetRow;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import com.coremasterkb.serving.operator.mapper.EvidenceSourceV2Mapper;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 批次8 R7 inspect_knowledge 契约（25 号 §8.3）：capabilities 从 v2 表事实推导
 * （can_navigate=nodes+parent 边、can_query_structured=ready 表格、can_aggregate=数值列）、
 * 资产清单带 st_ ref、表格资产返回完整字段 schema。
 */
@DisplayName("InspectService")
class InspectServiceTest {

    private static final String SNAP = "snap-1";
    private static final String ASSET = "doc:/spec#table:tbl:3";
    private static final String ST = "st_abcdefgh";

    private StructureRefService refService;
    private StructureToolMapper toolMapper;
    private EvidenceSourceV2Mapper sourceMapper;
    private StructuredQueryService queryService;
    private EvidenceRefCodec codec;
    private InspectService service;

    @BeforeEach
    void setUp() {
        refService = mock(StructureRefService.class);
        toolMapper = mock(StructureToolMapper.class);
        sourceMapper = mock(EvidenceSourceV2Mapper.class);
        queryService = mock(StructuredQueryService.class);
        codec = EvidenceRefCodec.forSecret("test-secret");
        service = new InspectService(refService, toolMapper, sourceMapper, queryService, codec);

        when(sourceMapper.selectDocumentSources(anyList())).thenReturn(List.of());
        // anyAggregate 扫描走 queryService.schemaOf（默认含一个数值列 → can_aggregate=true）
        when(queryService.schemaOf(anyString(), any())).thenReturn(
                new StructuredQueryService.TableSchema(ASSET, "ready", List.of(
                        new StructuredQueryService.FieldSchema("最大功耗", "number", true, true,
                                List.of("avg"))), 2));
        // v2 事实默认：有节点 + parent 边 + 1 个 ready 表格
        when(toolMapper.countNodesByType(SNAP, "section")).thenReturn(2);
        when(toolMapper.countNodesByType(SNAP, "segment")).thenReturn(10);
        when(toolMapper.countEdgesByRelation(SNAP, "parent")).thenReturn(12);
        when(toolMapper.selectSnapshotTableAssets(eq(SNAP), anyInt()))
                .thenReturn(List.of(asset("ready")));
    }

    @Test
    @DisplayName("doc_ ref：capabilities 四能力 + 资产清单（st_ ref 可直接喂 query）")
    void documentInspect() {
        when(refService.resolve(anyString(), anyString(), anyList(), anyString()))
                .thenReturn(new EvidenceRefResolver.ResolvedRef(
                        SNAP, EvidenceRefResolver.RefKind.DOCUMENT, "doc:/spec"));

        var out = service.inspect("doc_x", "odn", List.of("kb-1"), "alice");

        assertThat(out.ref_kind()).isEqualTo("document_ref");
        assertThat(out.capabilities())
                .containsEntry("can_navigate", true)
                .containsEntry("can_query_structured", true)
                .containsEntry("can_read_document", true);
        assertThat(out.assets()).hasSize(1);
        assertThat(out.assets().get(0).ref()).isEqualTo(codec.encodeStructure(SNAP, ASSET));
        assertThat(out.relations()).contains("children", "descendants");
    }

    @Test
    @DisplayName("无结构边 → can_navigate=false，relations 空")
    void navigateCapabilityRequiresParentEdges() {
        when(toolMapper.countEdgesByRelation(SNAP, "parent")).thenReturn(0);
        when(refService.resolve(anyString(), anyString(), anyList(), anyString()))
                .thenReturn(new EvidenceRefResolver.ResolvedRef(
                        SNAP, EvidenceRefResolver.RefKind.DOCUMENT, "doc:/spec"));

        var out = service.inspect("doc_x", "odn", List.of("kb-1"), "alice");
        assertThat(out.capabilities()).containsEntry("can_navigate", false);
        assertThat(out.relations()).isEmpty();
    }

    @Test
    @DisplayName("st_ 指向表格资产：完整字段 schema（display name/type/operations）+ can_aggregate")
    void tableAssetInspectReturnsSchema() {
        when(refService.resolve(anyString(), anyString(), anyList(), anyString()))
                .thenReturn(new EvidenceRefResolver.ResolvedRef(
                        SNAP, EvidenceRefResolver.RefKind.STRUCTURE, ASSET));
        when(toolMapper.selectTableAssetByAssetRef(SNAP, ASSET)).thenReturn(asset("ready"));
        when(queryService.schemaOf(eq(SNAP), any())).thenReturn(
                new StructuredQueryService.TableSchema(ASSET, "ready", List.of(
                        new StructuredQueryService.FieldSchema("型号", "text", true, false,
                                List.of("eq", "ne", "in", "contains", "is_null", "count")),
                        new StructuredQueryService.FieldSchema("最大功耗", "number", true, true,
                                List.of("eq", "sum", "avg"))), 2));

        var out = service.inspect(ST, "odn", List.of("kb-1"), "alice");

        assertThat(out.capabilities())
                .containsEntry("can_query_structured", true)
                .containsEntry("can_aggregate", true);
        assertThat(out.assets()).hasSize(1);
        assertThat(out.assets().get(0).schema()).hasSize(2);
        assertThat(out.assets().get(0).schema().get(1).value_type()).isEqualTo("number");
        assertThat(out.assets().get(0).schema().get(1).operations()).contains("avg");
    }

    @Test
    @DisplayName("ev_ 指向证据：按 canonical target 披露 node_type/evidence_type")
    void evidenceInspect() {
        when(refService.resolve(anyString(), anyString(), anyList(), anyString()))
                .thenReturn(new EvidenceRefResolver.ResolvedRef(
                        SNAP, EvidenceRefResolver.RefKind.EVIDENCE, "doc:/spec#seg:3"));
        UnitV2Row rep = new UnitV2Row();
        rep.setSnapshotId(SNAP);
        rep.setRepresentationId("rep-1");
        rep.setRepresentationType("prose");
        rep.setTargetType("segment");
        rep.setTargetRef("doc:/spec#seg:3");
        rep.setCanonicalEvidenceId("doc:/spec#seg:3");
        when(sourceMapper.selectCanonicalRepresentations(List.of(SNAP), List.of("doc:/spec#seg:3")))
                .thenReturn(List.of(rep));

        var out = service.inspect("ev_x", "odn", List.of("kb-1"), "alice");

        assertThat(out.ref_kind()).isEqualTo("evidence_ref");
        assertThat(out.node_type()).isEqualTo("segment");
        assertThat(out.evidence_type()).isEqualTo("prose");
    }

    @Test
    @DisplayName("ev_ 无 returnable 表示 → invalid_ref")
    void evidenceWithoutSourceRejected() {
        when(refService.resolve(anyString(), anyString(), anyList(), anyString()))
                .thenReturn(new EvidenceRefResolver.ResolvedRef(
                        SNAP, EvidenceRefResolver.RefKind.EVIDENCE, "gone"));
        when(sourceMapper.selectCanonicalRepresentations(anyList(), anyList()))
                .thenReturn(List.of());

        org.assertj.core.api.Assertions.assertThatThrownBy(
                        () -> service.inspect("ev_x", "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> ((StructureToolException) e).code())
                .isEqualTo("invalid_ref");
    }

    private static TableAssetRow asset(String readiness) {
        TableAssetRow a = new TableAssetRow();
        a.setSnapshotId(SNAP);
        a.setAssetRef(ASSET);
        a.setAssetType("table");
        a.setTableRef("tbl:3");
        a.setColumnsJson("[\"型号\",\"最大功耗\"]");
        a.setRowCount(2);
        a.setReadiness(readiness);
        return a;
    }
}
