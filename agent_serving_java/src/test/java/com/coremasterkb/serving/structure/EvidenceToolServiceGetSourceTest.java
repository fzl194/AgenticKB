package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.result.EvidenceDocumentRow;
import com.coremasterkb.serving.mapper.result.SourceLocatorRow;
import com.coremasterkb.serving.operator.mapper.EvidenceSourceV2Mapper;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import com.coremasterkb.serving.operator.operators.output.EvidenceHydrateOperator;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * A1 来源导航解析（37 号 P0-6 / 38 号 §3.3）：ev_ ref → 网页跳转锚。
 *
 * <p>覆盖：locator 行 + 文档行（含共享快照按 kb 消歧）→ document_id/大纲锚/
 * 表格锚；section_only 行只出锚不出 locator 对象；无可见文档链接 → invalid_ref。</p>
 */
@DisplayName("EvidenceToolService.getSource (A1)")
class EvidenceToolServiceGetSourceTest {

    private static final String SNAP = "snap-1";
    private static final String CANONICAL = "doc:/spec#table_row:t1:3";

    private EvidenceSourceV2Mapper sourceMapper;
    private EvidenceToolService service;

    @BeforeEach
    void setUp() {
        EvidenceRefCodec codec = EvidenceRefCodec.forSecret("test-secret");
        StructureRefService refService = mock(StructureRefService.class);
        StructureToolMapper toolMapper = mock(StructureToolMapper.class);
        sourceMapper = mock(EvidenceSourceV2Mapper.class);
        EvidenceHydrateOperator hydrate = mock(EvidenceHydrateOperator.class);
        service = new EvidenceToolService(refService, sourceMapper, toolMapper, hydrate, codec);

        when(refService.resolve(anyString(), anyString(),
                org.mockito.ArgumentMatchers.<List<String>>any(), anyString()))
                .thenReturn(new EvidenceRefResolver.ResolvedRef(
                        SNAP, EvidenceRefResolver.RefKind.EVIDENCE, CANONICAL));
    }

    private static SourceLocatorRow locatorRow(String kind) {
        SourceLocatorRow row = new SourceLocatorRow();
        row.setSnapshotId(SNAP);
        row.setRepresentationId(CANONICAL);
        row.setTargetRef(CANONICAL);
        row.setDocumentRef("doc:/spec");
        row.setSourceFormat("xlsx");
        row.setLocatorKind(kind);
        row.setSectionPath("第二章 > 告警表");
        row.setSectionElementId("h-2");
        row.setSheet("告警表");
        row.setCell("B7");
        row.setTableRef("t1");
        row.setRowIndex(3);
        return row;
    }

    private static EvidenceDocumentRow docRow(String kbId, String documentId) {
        EvidenceDocumentRow doc = new EvidenceDocumentRow();
        doc.setSnapshotId(SNAP);
        doc.setDocumentId(documentId);
        doc.setDocumentKey("doc:/spec");
        doc.setDocumentName("spec.xlsx");
        doc.setKbId(kbId);
        doc.setKbName("规范库-" + kbId);
        doc.setRelativePath("规范/spec.xlsx");
        return doc;
    }

    @Test
    @DisplayName("sheet_cell locator → document_id/大纲锚/表格锚/locator 全返回")
    void resolvesFullNavigationPayload() {
        when(sourceMapper.selectSourceLocators(anyList(), anyList()))
                .thenReturn(List.of(locatorRow("sheet_cell")));
        when(sourceMapper.selectDocumentSources(anyList()))
                .thenReturn(List.of(docRow("kb-1", "doc-uuid-1")));

        EvidenceToolService.SourceNavigation nav =
                service.getSource("ev_x", "generic", List.of("kb-1"), "alice");

        assertThat(nav.document_id()).isEqualTo("doc-uuid-1");
        assertThat(nav.kb_id()).isEqualTo("kb-1");
        assertThat(nav.section_element_id()).isEqualTo("h-2");
        assertThat(nav.section_path()).isEqualTo("第二章 > 告警表");
        assertThat(nav.table_ref()).isEqualTo("t1");
        assertThat(nav.row_index()).isEqualTo(3);
        assertThat(nav.locator()).isNotNull();
        assertThat(nav.locator().kind()).isEqualTo("sheet_cell");
        assertThat(nav.locator().sheet()).isEqualTo("告警表");
        assertThat(nav.locator().cell()).isEqualTo("B7");
    }

    @Test
    @DisplayName("共享快照多文档：按请求 kb 消歧 document_id")
    void disambiguatesSharedSnapshotByKb() {
        when(sourceMapper.selectSourceLocators(anyList(), anyList()))
                .thenReturn(List.of(locatorRow("sheet_cell")));
        when(sourceMapper.selectDocumentSources(anyList())).thenReturn(List.of(
                docRow("kb-other", "doc-uuid-other"),
                docRow("kb-1", "doc-uuid-1")));

        EvidenceToolService.SourceNavigation nav =
                service.getSource("ev_x", "generic", List.of("kb-1"), "alice");

        assertThat(nav.document_id()).isEqualTo("doc-uuid-1");
        assertThat(nav.kb_id()).isEqualTo("kb-1");
    }

    @Test
    @DisplayName("section_only 行只出锚（section_element_id）不出 locator")
    void sectionOnlyYieldsAnchorWithoutLocator() {
        when(sourceMapper.selectSourceLocators(anyList(), anyList()))
                .thenReturn(List.of(locatorRow("section_only")));
        when(sourceMapper.selectDocumentSources(anyList()))
                .thenReturn(List.of(docRow("kb-1", "doc-uuid-1")));

        EvidenceToolService.SourceNavigation nav =
                service.getSource("ev_x", "generic", null, "alice");

        assertThat(nav.section_element_id()).isEqualTo("h-2");
        assertThat(nav.locator()).isNull();
    }

    @Test
    @DisplayName("无 locator 行（旧快照）仍可导航到文档，locator 为空")
    void missingLocatorRowStillNavigates() {
        when(sourceMapper.selectSourceLocators(anyList(), anyList()))
                .thenReturn(List.of());
        when(sourceMapper.selectDocumentSources(anyList()))
                .thenReturn(List.of(docRow("kb-1", "doc-uuid-1")));

        EvidenceToolService.SourceNavigation nav =
                service.getSource("ev_x", "generic", null, "alice");

        assertThat(nav.document_id()).isEqualTo("doc-uuid-1");
        assertThat(nav.section_element_id()).isNull();
        assertThat(nav.locator()).isNull();
    }

    @Test
    @DisplayName("快照无可见文档链接 → invalid_ref（400，可修正错误）")
    void invisibleDocumentLinkRejected() {
        when(sourceMapper.selectSourceLocators(anyList(), anyList()))
                .thenReturn(List.of());
        when(sourceMapper.selectDocumentSources(anyList())).thenReturn(List.of());

        org.assertj.core.api.Assertions.assertThatThrownBy(
                        () -> service.getSource("ev_x", "generic",
                                List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .satisfies(e -> assertThat(((StructureToolException) e).status())
                        .isEqualTo(org.springframework.http.HttpStatus.BAD_REQUEST));
    }
}
