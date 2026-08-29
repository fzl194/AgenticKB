package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.result.TableAssetRow;
import com.coremasterkb.serving.mapper.result.TableCellRow;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper.Criterion;
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
import static org.mockito.Mockito.when;

/**
 * 批次8 R7 structured_query 契约（25 号 §6.11/§7.2）：
 * 白名单字段（unknown_field）、类型校验（type_mismatch）、操作能力（unsupported_operation）、
 * limit 上限（result_too_large）、readiness 门（structured_query_unavailable）、
 * 数值列判定与参数化 criteria 构造（SQL 注入面收敛在绑定参数）。
 */
@DisplayName("StructuredQueryService")
class StructuredQueryServiceTest {

    private static final String SNAP = "snap-1";
    private static final String ASSET_REF = "doc:/spec#table:tbl:3";
    private static final String ST_REF = "st_abcdefgh";

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
        // 默认：ready 表格，两列（型号=文本，最大功耗=全数值）
        when(toolMapper.selectTableAssetByAssetRef(SNAP, ASSET_REF)).thenReturn(asset("ready"));
        when(toolMapper.selectCellsForTyping(eq(SNAP), eq("tbl:3"), anyInt()))
                .thenReturn(List.of(
                        header("型号"), header("最大功耗"),
                        cell("型号", "OLT-1"), cell("最大功耗", "65"),
                        cell("型号", "OLT-2"), cell("最大功耗", "100.5")));
    }

    // ---- rows mode ------------------------------------------------------------

    @Test
    @DisplayName("行查询：select 投影 + where 数值比较（criteria 参数化，numeric=true）")
    void rowsModeWithNumericFilter() {
        when(toolMapper.countStructuredRows(eq(SNAP), eq("tbl:3"), anyList())).thenReturn(0L);
        when(toolMapper.selectStructuredRows(eq(SNAP), eq("tbl:3"), anyList(), any(), any(),
                anyBoolean(), eq(51), eq(0)))
                .thenReturn(List.of(row(1, "{\"型号\": \"OLT-1\", \"最大功耗\": \"65\"}")));

        var out = service.query(ST_REF,
                spec("{\"select\": [\"型号\"], \"where\": [{\"field\": \"最大功耗\", \"op\": \"lte\", \"value\": 100}]}"),
                "odn", List.of("kb-1"), "alice");

        assertThat(out.rows()).hasSize(1);
        assertThat(out.rows().get(0)).containsEntry("型号", "OLT-1");

        @SuppressWarnings("unchecked")
        ArgumentCaptor<List<Criterion>> cap = ArgumentCaptor.forClass(List.class);
        org.mockito.Mockito.verify(toolMapper)
                .selectStructuredRows(eq(SNAP), eq("tbl:3"), cap.capture(), any(), any(),
                        anyBoolean(), eq(51), eq(0));
        Criterion c = cap.getValue().get(0);
        assertThat(c.getField()).isEqualTo("最大功耗"); // 字段名只作绑定参数
        assertThat(c.getOp()).isEqualTo("lte");
        assertThat(c.getValue()).isEqualTo(100.0);
        assertThat(c.isNumeric()).isTrue();
    }

    @Test
    @DisplayName("字段白名单：未知字段 → unknown_field，details 带允许清单")
    void unknownFieldRejected() {
        assertThatThrownBy(() -> service.query(ST_REF,
                spec("{\"where\": [{\"field\": \"型号'; DROP TABLE x;--\", \"op\": \"eq\", \"value\": \"1\"}]}"),
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .satisfies(e -> {
                    StructureToolException ste = (StructureToolException.class.cast(e));
                    assertThat(ste.code()).isEqualTo("unknown_field");
                    assertThat(ste.details()).containsKey("allowed_fields");
                    // 注入载荷不进任何 SQL 片段——它在白名单校验即被拒绝
                    org.mockito.Mockito.verify(toolMapper, org.mockito.Mockito.never())
                            .selectStructuredRows(any(), any(), any(), any(), any(), anyBoolean(),
                                    anyInt(), anyInt());
                });
    }

    @Test
    @DisplayName("类型校验：数值列传字符串数字可容错，传非数字 → type_mismatch")
    void numericTypeCoercion() {
        when(toolMapper.selectStructuredRows(eq(SNAP), eq("tbl:3"), anyList(), any(), any(),
                anyBoolean(), anyInt(), anyInt()))
                .thenReturn(List.of());
        when(toolMapper.countStructuredRows(eq(SNAP), eq("tbl:3"), anyList())).thenReturn(0L);

        // 字符串数字 → 容错（Agent 常回传字符串化 JSON 数字）
        service.query(ST_REF,
                spec("{\"where\": [{\"field\": \"最大功耗\", \"op\": \"gt\", \"value\": \"1,200\"}]}"),
                "odn", List.of("kb-1"), "alice");

        // 非数字 → type_mismatch
        assertThatThrownBy(() -> service.query(ST_REF,
                spec("{\"where\": [{\"field\": \"最大功耗\", \"op\": \"gt\", \"value\": \"大\"}]}"),
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> StructureToolException.class.cast(e).code())
                .isEqualTo("type_mismatch");
    }

    @Test
    @DisplayName("能力校验：文本列 lt → unsupported_operation；数值列 contains → unsupported_operation")
    void operationCapabilityEnforced() {
        assertThatThrownBy(() -> service.query(ST_REF,
                spec("{\"where\": [{\"field\": \"型号\", \"op\": \"lt\", \"value\": 1}]}"),
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> StructureToolException.class.cast(e).code())
                .isEqualTo("unsupported_operation");

        assertThatThrownBy(() -> service.query(ST_REF,
                spec("{\"where\": [{\"field\": \"最大功耗\", \"op\": \"contains\", \"value\": \"65\"}]}"),
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> StructureToolException.class.cast(e).code())
                .isEqualTo("unsupported_operation");
    }

    @Test
    @DisplayName("limit 超上限 200 → result_too_large")
    void limitGuard() {
        assertThatThrownBy(() -> service.query(ST_REF, spec("{\"limit\": 10000}"),
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> StructureToolException.class.cast(e).code())
                .isEqualTo("result_too_large");
    }

    // ---- readiness 门 ------------------------------------------------------------

    @Test
    @DisplayName("readiness=insufficient → structured_query_unavailable（退回 search/get_evidence）")
    void readinessGate() {
        when(toolMapper.selectTableAssetByAssetRef(SNAP, ASSET_REF)).thenReturn(asset("insufficient"));

        assertThatThrownBy(() -> service.query(ST_REF, spec("{}"),
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .satisfies(e -> assertThat(StructureToolException.class.cast(e).code())
                        .isEqualTo("structured_query_unavailable"));
    }

    @Test
    @DisplayName("st_ 非 table 资产（无 asset 行）→ unsupported_operation")
    void nonTableAssetRejected() {
        when(toolMapper.selectTableAssetByAssetRef(SNAP, ASSET_REF)).thenReturn(null);

        assertThatThrownBy(() -> service.query(ST_REF, spec("{}"),
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> StructureToolException.class.cast(e).code())
                .isEqualTo("unsupported_operation");
    }

    // ---- aggregates ------------------------------------------------------------

    @Test
    @DisplayName("数值列聚合：avg 参数化下推 + row_count 附带")
    void numericAggregate() {
        when(toolMapper.aggregateStructuredRows(eq(SNAP), eq("tbl:3"), anyList(),
                eq("avg"), eq("最大功耗")))
                .thenReturn(new StructureToolMapper.AggregateRow(82.75));
        when(toolMapper.countStructuredRows(eq(SNAP), eq("tbl:3"), anyList())).thenReturn(2L);

        var out = service.query(ST_REF,
                spec("{\"aggregate\": {\"op\": \"avg\", \"field\": \"最大功耗\"}}"),
                "odn", List.of("kb-1"), "alice");

        assertThat(out.aggregate()).isNotNull();
        assertThat(out.aggregate().op()).isEqualTo("avg");
        assertThat(out.aggregate().value()).isEqualTo(82.75);
        assertThat(out.aggregate().row_count()).isEqualTo(2L);
    }

    @Test
    @DisplayName("文本列 sum → unsupported_operation（仅 count）")
    void textColumnAggregateRejected() {
        assertThatThrownBy(() -> service.query(ST_REF,
                spec("{\"aggregate\": {\"op\": \"sum\", \"field\": \"型号\"}}"),
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> StructureToolException.class.cast(e).code())
                .isEqualTo("unsupported_operation");
    }

    @Test
    @DisplayName("未知聚合 op → unsupported_operation")
    void unknownAggregateOp() {
        assertThatThrownBy(() -> service.query(ST_REF,
                spec("{\"aggregate\": {\"op\": \"median\", \"field\": \"最大功耗\"}}"),
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> StructureToolException.class.cast(e).code())
                .isEqualTo("unsupported_operation");
    }

    @Test
    @DisplayName("值扫描截断（cells 超 cap）→ 列保守降级文本，聚合被拒")
    void truncatedTypingDegradesToText() {
        // 让扫描截断：返回满 cap 行数
        java.util.List<TableCellRow> many = new java.util.ArrayList<>();
        many.add(header("最大功耗"));
        for (int i = 0; i < StructuredQueryService.TYPING_SCAN_CAP; i++) {
            many.add(cell("最大功耗", String.valueOf(i)));
        }
        when(toolMapper.selectCellsForTyping(eq(SNAP), eq("tbl:3"), anyInt())).thenReturn(many);

        assertThatThrownBy(() -> service.query(ST_REF,
                spec("{\"aggregate\": {\"op\": \"sum\", \"field\": \"最大功耗\"}}"),
                "odn", List.of("kb-1"), "alice"))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> StructureToolException.class.cast(e).code())
                .isEqualTo("unsupported_operation");
    }

    // ---- helpers ------------------------------------------------------------------------

    private static StructuredQueryService.QuerySpec spec(String json) {
        try {
            var n = new com.fasterxml.jackson.databind.ObjectMapper().readTree(json);
            var where = new java.util.ArrayList<StructuredQueryService.WhereClause>();
            if (n.has("where")) {
                for (var w : n.get("where")) {
                    where.add(new StructuredQueryService.WhereClause(
                            w.path("field").asText(null), w.path("op").asText(null), w.get("value")));
                }
            }
            var orderBy = new java.util.ArrayList<StructuredQueryService.OrderClause>();
            if (n.has("order_by")) {
                for (var o : n.get("order_by")) {
                    orderBy.add(new StructuredQueryService.OrderClause(
                            o.path("field").asText(null), o.path("direction").asText(null)));
                }
            }
            StructuredQueryService.Aggregate agg = null;
            if (n.has("aggregate") && n.get("aggregate").has("op")) {
                agg = new StructuredQueryService.Aggregate(
                        n.get("aggregate").path("op").asText(),
                        n.get("aggregate").path("field").asText(null));
            }
            return new StructuredQueryService.QuerySpec(
                    n.has("select") && n.get("select").isArray()
                            ? List.copyOf(new java.util.ArrayList<String>() {{
                                n.get("select").forEach(s -> add(s.asText())); }}) : null,
                    where, orderBy,
                    n.hasNonNull("limit") ? n.get("limit").asInt() : null,
                    n.hasNonNull("cursor") ? n.get("cursor").asText() : null,
                    agg);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private static TableAssetRow asset(String readiness) {
        TableAssetRow a = new TableAssetRow();
        a.setSnapshotId(SNAP);
        a.setAssetRef(ASSET_REF);
        a.setAssetType("table");
        a.setTableRef("tbl:3");
        a.setColumnsJson("[\"型号\",\"最大功耗\"]");
        a.setRowCount(2);
        a.setReadiness(readiness);
        return a;
    }

    private static TableCellRow header(String col) {
        TableCellRow c = new TableCellRow();
        c.setSnapshotId(SNAP);
        c.setTableRef("tbl:3");
        c.setRowIndex(0);
        c.setColumnIndex(0);
        c.setColumnName(col);
        c.setValue(col);
        c.setIsHeader(true);
        return c;
    }

    private static TableCellRow cell(String col, String value) {
        TableCellRow c = header(col);
        c.setIsHeader(false);
        c.setValue(value);
        return c;
    }

    private static StructureToolMapper.StructuredRow row(int idx, String cellsJson) {
        return new StructureToolMapper.StructuredRow(idx, cellsJson);
    }
}
