package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import com.coremasterkb.serving.mapper.result.TableAssetRow;
import com.coremasterkb.serving.mapper.result.TableCellRow;
import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.structure.StructuredQueryService.QuerySpec;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * P2 批次回归（Codex 审查 11/12/13/14/17）。
 *
 * <ul>
 *   <li>P2-11：分页排序稳定——未指定 order_by 时 SQL 必须默认
 *       {@code row_index ASC}（cursor=offset 分页的前后页一致前提）；</li>
 *   <li>P2-12：真实日期语义——{@code 2026-99-99}/{@code 2026-02-31} 必须
 *       type_mismatch（正则形状合法但日历非法）；</li>
 *   <li>P2-13：typing 扫描截断时不得采信声明类型（截断=后半表未见，
 *       声明可能只覆盖前 2000 cells）；</li>
 *   <li>P2-14：DSL 形状严格——非 object query、非数组 select/where/order_by、
 *       非 object 的 item 全部 typed 400，不静默当缺省；</li>
 *   <li>P2-17：previous/next 兄弟过滤 node_type（service 层断言
 *       selectSiblings 调用带当前节点类型）。</li>
 * </ul>
 */
@DisplayName("Structure query P2 contracts (11/12/13/14/17)")
class StructureQueryP2ContractTest {

    private static final String SNAP = "snap-1";
    private static final String ASSET_REF = "doc:/spec#table:tbl:3";
    private static final String ST_REF = "st_abcdefgh";

    private final ObjectMapper mapper = new ObjectMapper();

    private StructureRefService refService;
    private StructureToolMapper toolMapper;
    private StructuredQueryService service;

    @BeforeEach
    void setUp() {
        refService = mock(StructureRefService.class);
        toolMapper = mock(StructureToolMapper.class);
        service = new StructuredQueryService(refService, toolMapper);

        when(refService.resolve(anyString(), anyString(), anyList(), anyString()))
                .thenReturn(new EvidenceRefResolver.ResolvedRef(
                        SNAP, EvidenceRefResolver.RefKind.STRUCTURE, ASSET_REF));
        when(toolMapper.selectTableAssetByAssetRef(SNAP, ASSET_REF))
                .thenReturn(asset("ready"));
        when(toolMapper.selectCellsForTyping(eq(SNAP), eq("tbl:3"), anyInt()))
                .thenReturn(List.of(
                        header("型号"), header("投产日期"),
                        cell("型号", "OLT-1"), cell("投产日期", "2026-01-05", "date"),
                        cell("型号", "OLT-2"), cell("投产日期", "2025-12-31", "date")));
        when(toolMapper.countStructuredRows(eq(SNAP), eq("tbl:3"), anyList()))
                .thenReturn(0L);
    }

    private QuerySpec spec(String json) throws Exception {
        return StructureQueryDsl.parseSpec(mapper.readTree(json));
    }

    // ---- P2-11: 默认 row_index 稳定排序 -------------------------------------

    @Test
    @DisplayName("P2-11: no order_by → default ORDER BY row_index ASC")
    void defaultOrderByRowIndex() throws Exception {
        when(toolMapper.selectStructuredRows(eq(SNAP), eq("tbl:3"), anyList(),
                any(), any(), anyBoolean(), anyInt(), anyInt()))
                .thenReturn(List.of());

        service.query(ST_REF, spec("{\"where\": []}"), "odn", List.of("kb-1"), "a");

        ArgumentCaptor<String> orderField = ArgumentCaptor.forClass(String.class);
        verify(toolMapper).selectStructuredRows(eq(SNAP), eq("tbl:3"), anyList(),
                orderField.capture(), any(), anyBoolean(), anyInt(), anyInt());
        assertThat(orderField.getValue()).isNull(); // physical default, not a user column name
    }

    @Test
    @DisplayName("P2-11: explicit order persists across cursor pages (service passthrough)")
    void explicitOrderStableAcrossPages() throws Exception {
        when(toolMapper.selectStructuredRows(eq(SNAP), eq("tbl:3"), anyList(),
                any(), any(), anyBoolean(), anyInt(), anyInt()))
                .thenReturn(List.of());

        service.query(ST_REF, spec(
                        "{\"order_by\": [{\"field\": \"型号\", \"direction\": \"desc\"}]}"),
                "odn", List.of("kb-1"), "a");

        ArgumentCaptor<String> orderField = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> orderDir = ArgumentCaptor.forClass(String.class);
        verify(toolMapper).selectStructuredRows(eq(SNAP), eq("tbl:3"), anyList(),
                orderField.capture(), orderDir.capture(), anyBoolean(), anyInt(), anyInt());
        assertThat(orderField.getValue()).isEqualTo("型号");
        assertThat(orderDir.getValue()).isEqualTo("desc");
    }

    // ---- P2-12: 真实日期语义 --------------------------------------------------

    @Test
    @DisplayName("P2-12: 2026-99-99 rejected as type_mismatch (calendar-invalid)")
    void invalidCalendarDateRejected() throws Exception {
        assertThatThrownBy(() -> service.query(ST_REF, spec(
                        "{\"where\": [{\"field\": \"投产日期\", \"op\": \"gte\", \"value\": \"2026-99-99\"}]}"),
                "odn", List.of("kb-1"), "a"))
                .isInstanceOf(StructureToolException.class)
                .satisfies(e -> assertThat(
                        ((StructureToolException) e).code()).isEqualTo("type_mismatch"));
    }

    @Test
    @DisplayName("P2-12: 2026-02-31 rejected as type_mismatch (not a real date)")
    void february31Rejected() throws Exception {
        assertThatThrownBy(() -> service.query(ST_REF, spec(
                        "{\"where\": [{\"field\": \"投产日期\", \"op\": \"eq\", \"value\": \"2026-02-31\"}]}"),
                "odn", List.of("kb-1"), "a"))
                .isInstanceOf(StructureToolException.class)
                .satisfies(e -> assertThat(
                        ((StructureToolException) e).code()).isEqualTo("type_mismatch"));
    }

    @Test
    @DisplayName("P2-12: valid leap-day date accepted")
    void leapDayAccepted() throws Exception {
        when(toolMapper.selectStructuredRows(eq(SNAP), eq("tbl:3"), anyList(),
                any(), any(), anyBoolean(), anyInt(), anyInt()))
                .thenReturn(List.of());

        var out = service.query(ST_REF, spec(
                        "{\"where\": [{\"field\": \"投产日期\", \"op\": \"eq\", \"value\": \"2024-02-29\"}]}"),
                "odn", List.of("kb-1"), "a");
        assertThat(out).isNotNull();
    }

    // ---- P2-13: 截断保守降级 --------------------------------------------------

    @Test
    @DisplayName("P2-13: typing scan truncated → declared type not trusted for that column")
    void truncatedScanDistrustsDeclaredType() {
        when(toolMapper.selectCellsForTyping(eq(SNAP), eq("tbl:3"), anyInt()))
                .thenReturn(truncatedCells("功耗", "number")); // 恰好 TYPING_SCAN_CAP 行

        var schema = service.schemaOf(SNAP, asset("ready"));
        var field = schema.columns().stream()
                .filter(f -> f.name().equals("功耗")).findFirst().orElseThrow();
        // 截断=后半表未见：即使前 2000 全声明 number 也不得独断（回退 text 保守）
        assertThat(field.value_type()).isEqualTo("text");
    }

    private List<com.coremasterkb.serving.mapper.result.TableCellRow> truncatedCells(
            String col, String declaredType) {
        var out = new java.util.ArrayList<com.coremasterkb.serving.mapper.result.TableCellRow>();
        out.add(header(col));
        for (int i = 0; i < StructuredQueryService.TYPING_SCAN_CAP; i++) {
            out.add(cell(col, "1", declaredType));
        }
        return out;
    }

    // ---- P2-14: DSL 形状严格校验 ----------------------------------------------

    @Test
    @DisplayName("P2-14: non-object query body → typed 400, not silent default")
    void nonObjectQueryRejected() {
        assertThatThrownBy(() -> StructureQueryDsl.parseSpec(
                        mapper.readTree("[1,2,3]")))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> StructureQueryDsl.parseSpec(
                        mapper.readTree("\"just a string\"")))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("P2-14: select/where/order_by must be arrays → typed 400")
    void nonArrayClausesRejected() throws Exception {
        assertThatThrownBy(() -> spec("{\"select\": \"型号\"}"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("select");
        assertThatThrownBy(() -> spec("{\"where\": {\"field\": \"x\"}}"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("where");
        assertThatThrownBy(() -> spec("{\"order_by\": {\"field\": \"x\"}}"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("order_by");
    }

    @Test
    @DisplayName("P2-14: where/order items must be objects with typed field/op")
    void malformedItemsRejected() throws Exception {
        assertThatThrownBy(() -> spec("{\"where\": [\"not-an-object\"]}"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> spec("{\"where\": [{\"op\": \"eq\", \"value\": 1}]}"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("field");
        assertThatThrownBy(() -> spec("{\"order_by\": [42]}"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("P2-14: valid minimal spec still parses")
    void validMinimalSpecParses() throws Exception {
        var s = spec("{\"select\": [\"型号\"], \"where\": [], \"order_by\": []}");
        assertThat(s.select()).containsExactly("型号");
        assertThat(s.where()).isEmpty();
    }

    // ---- P2-17: 兄弟同类型 ----------------------------------------------------

    @Test
    @DisplayName("P2-17: previous/next only among same node_type siblings")
    void siblingsFilteredByNodeType() {
        // refService.resolve 泛 mock 返回 ASSET_REF——本用例针对 section ref 覆写
        when(refService.resolve(eq("st_sec0"), anyString(), anyList(), anyString()))
                .thenReturn(new EvidenceRefResolver.ResolvedRef(
                        SNAP, EvidenceRefResolver.RefKind.STRUCTURE, "doc:/s#section:0"));
        when(toolMapper.selectNode(SNAP, "doc:/s#section:0"))
                .thenReturn(node("section", "doc:/s#section:0", "doc:/s#document", 0));
        when(toolMapper.selectSiblingsOfType(eq(SNAP), eq("doc:/s#document"),
                eq("section"), anyInt()))
                .thenReturn(List.of(
                        node("section", "doc:/s#section:0", "doc:/s#document", 0),
                        node("section", "doc:/s#section:1", "doc:/s#document", 1)));

        var codec = com.coremasterkb.serving.evidence.EvidenceRefCodec.forSecret("test-secret");
        var sourceMapper = mock(com.coremasterkb.serving.operator.mapper.EvidenceSourceV2Mapper.class);
        var nav = new StructureNavigateService(refService, toolMapper, sourceMapper, codec);
        nav.navigate("st_sec0", "next", null, null, null,
                "odn", List.of("kb-1"), "a");

        // P2-17：siblings 查询必须带 node_type 过滤（章节导航只回章节）
        verify(toolMapper).selectSiblingsOfType(
                eq(SNAP), eq("doc:/s#document"), eq("section"), anyInt());
    }

    // ---- helpers --------------------------------------------------------------

    private TableAssetRow asset(String readiness) {
        TableAssetRow a = new TableAssetRow();
        a.setSnapshotId(SNAP);
        a.setAssetRef(ASSET_REF);
        a.setAssetType("table");
        a.setTableRef("tbl:3");
        a.setColumnsJson("[]");
        a.setRowCount(3);
        a.setReadiness(readiness);
        return a;
    }

    private TableCellRow header(String name) {
        return cell(name, name, null, true);
    }

    private TableCellRow cell(String col, String value) {
        return cell(col, value, null, false);
    }

    private TableCellRow cell(
            String col, String value, String valueType) {
        return cell(col, value, valueType, false);
    }

    private TableCellRow cell(
            String col, String value, String valueType, boolean header) {
        TableCellRow c = new TableCellRow();
        c.setColumnName(col);
        c.setValue(value);
        c.setIsHeader(header);
        c.setValueType(valueType);
        return c;
    }

    private StructureNodeRow node(
            String type, String ref, String parentRef, Integer ordinal) {
        StructureNodeRow n = new StructureNodeRow();
        n.setNodeType(type);
        n.setRef(ref);
        n.setParentRef(parentRef);
        n.setOrdinal(ordinal);
        return n;
    }
}
