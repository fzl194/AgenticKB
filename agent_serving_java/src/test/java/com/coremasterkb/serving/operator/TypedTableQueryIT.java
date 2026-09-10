package com.coremasterkb.serving.operator;

import com.coremasterkb.serving.AgentServingApplication;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.result.TableAssetRow;
import com.coremasterkb.serving.structure.StructureRefService;
import com.coremasterkb.serving.structure.StructuredQueryService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * A3 表格精确消费（39 号 §3.2）真 PG 验证：声明类型 + 规范值比较基准 +
 * date 序数语义。同一 SQL/服务即 MCP 通道——这里过了，网页/Agent 一致性
 * 由同源保证（E2E 再断言两通道同结果）。
 */
@SpringBootTest(classes = AgentServingApplication.class)
@ActiveProfiles("test-pg")
@Tag("pg-integration")
@DisplayName("A3 typed table query IT")
class TypedTableQueryIT {

    private static final String TOKEN = java.util.UUID.randomUUID().toString().substring(0, 8);
    private static final String SNAP = "snap-a3-" + TOKEN;
    private static final String KB = "kb-a3-" + TOKEN;
    private static final String DOC = "doc-a3-" + TOKEN;
    private static final String BUILD = "b-a3-" + TOKEN;
    private static final String TABLE_REF = "tbl:alarm";
    private static final String ASSET_REF = "alarm.xlsx#table:tbl:alarm";

    @Autowired
    private DataSource dataSource;

    @Autowired
    private StructureRefService refService;

    @Autowired
    private StructuredQueryService queryService;

    @Autowired
    private com.coremasterkb.serving.evidence.EvidenceRefCodec refCodec;

    private JdbcTemplate jdbc;
    private String stRef;

    @BeforeEach
    void setUp() throws Exception {
        try (Connection conn = dataSource.getConnection()) {
            assumeTrue(conn.isValid(3), "PostgreSQL not reachable — skipping");
        } catch (SQLException e) {
            assumeTrue(false, "PostgreSQL not reachable — skipping");
        }
        jdbc = new JdbcTemplate(dataSource);
        assumeTrue(tableExists("asset_table_cells"),
                "asset schema not present — skipping");

        jdbc.update(
                "INSERT INTO asset_structured_assets (snapshot_id, asset_ref, asset_type, "
                        + "table_ref, columns_json, row_count, readiness, schema_version, "
                        + "sheet_name) VALUES (?,?,?,?,?::jsonb,?,?,?,?)",
                SNAP, ASSET_REF, "table", TABLE_REF,
                "[\"告警码\",\"投产日期\",\"功耗\"]", 3, "ready", "asset-v2-1", "告警表");

        // 声明类型 + 规范值：日期展示值 2026/01/05 → 规范 2026-01-05；
        // 功耗千分位展示 → 规范数值
        insertCell(1, 0, "A1-101", true, null, null, "text");
        insertCell(1, 1, "2026/01/05", true, "2026-01-05", null, "date");
        insertCell(1, 2, "1,234.5", true, "1234.5", null, "number");
        insertCell(2, 0, "A1-102", true, null, null, "text");
        insertCell(2, 1, "2026/09/07", true, "2026-09-07", null, "date");
        insertCell(2, 2, "2,000", true, "2000", null, "number");
        insertCell(3, 0, "A1-103", true, null, null, "text");
        insertCell(3, 1, "2025/12/31", true, "2025-12-31", null, "date");
        insertCell(3, 2, "500.25", true, "500.25", null, "number");

        // 授权解析链：public KB → validated build → active membership（匿名可读）
        String owner = "owner-" + TOKEN;
        jdbc.update(
                "INSERT INTO kb_users (id, username, status, created_at) "
                        + "VALUES (?,?,'active','2026-01-01T00:00:00Z')",
                owner, "name-" + owner);
        jdbc.update(
                "INSERT INTO knowledge_bases (id, domain, name, owner_id, visibility, "
                        + "status, created_at, updated_at) "
                        + "VALUES (?,?,?,?, 'public','active',?,?)",
                KB, "cloud_core_network", KB, owner,
                "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z");
        jdbc.update(
                "INSERT INTO asset_documents (id, domain, document_key, document_name, "
                        + "created_at, kb_id) VALUES (?,?,?,?,?,?)",
                DOC, "cloud_core_network", "alarm-" + TOKEN, "告警表.xlsx",
                "2026-01-01T00:00:00Z", KB);
        jdbc.update(
                "INSERT INTO asset_document_snapshots (id, domain, normalized_content_hash, "
                        + "raw_content_hash, mime_type, title, created_at) "
                        + "VALUES (?,?,?,?,?,?,?)",
                SNAP, "cloud_core_network", "n-" + TOKEN, "r-" + TOKEN,
                "application/vnd.ms-excel", "告警表", "2026-01-01T00:00:00Z");
        jdbc.update(
                "INSERT INTO asset_builds (id, build_code, status, build_mode, domain, "
                        + "created_at, kb_id) VALUES (?,?,?,?,?,?,?)",
                BUILD, "bc-" + TOKEN, "validated", "full", "cloud_core_network",
                "2026-01-01T00:00:00Z", KB);
        jdbc.update(
                "INSERT INTO asset_build_document_snapshots (build_id, document_id, "
                        + "document_snapshot_id, selection_status, reason) "
                        + "VALUES (?,?,?,'active','test')",
                BUILD, DOC, SNAP);

        // 生成真实 st_ ref（同一编码器 = 生产路径）
        stRef = refCodec.encodeStructure(SNAP, ASSET_REF);
    }

    @AfterEach
    void cleanUp() {
        if (jdbc == null) return;
        jdbc.update("DELETE FROM asset_build_document_snapshots WHERE build_id = ?", BUILD);
        jdbc.update("DELETE FROM asset_builds WHERE id = ?", BUILD);
        jdbc.update("DELETE FROM asset_document_snapshots WHERE id = ?", SNAP);
        jdbc.update("DELETE FROM asset_documents WHERE id = ?", DOC);
        jdbc.update("DELETE FROM knowledge_bases WHERE id = ?", KB);
        jdbc.update("DELETE FROM kb_users WHERE id LIKE ?", "owner-" + TOKEN);
        jdbc.update("DELETE FROM asset_table_cells WHERE snapshot_id = ?", SNAP);
        jdbc.update("DELETE FROM asset_structured_assets WHERE snapshot_id = ?", SNAP);
    }

    @Test
    @DisplayName("声明类型成为 schema：date/number 列与能力面")
    void declaredTypesDriveSchema() {
        var schema = queryService.schemaOf(SNAP, assetRow());
        var byName = new java.util.HashMap<String, StructuredQueryService.FieldSchema>();
        schema.columns().forEach(f -> byName.put(f.name(), f));
        assertThat(byName.get("投产日期").value_type()).isEqualTo("date");
        assertThat(byName.get("功耗").value_type()).isEqualTo("number");
        assertThat(byName.get("告警码").value_type()).isEqualTo("text");
        assertThat(byName.get("投产日期").operations()).contains("gte", "min", "max");
        assertThat(byName.get("投产日期").operations()).doesNotContain("sum", "avg", "contains");
    }

    @Test
    @DisplayName("date 序数过滤走规范值：gte 2026-01-01 命中两行，展示值格式无关")
    void dateFilterOnNormalizedBasis() {
        var out = queryService.query(stRef,
                new StructuredQueryService.QuerySpec(
                        null,
                        List.of(new StructuredQueryService.WhereClause(
                                "投产日期", "gte",
                                com.fasterxml.jackson.databind.node.JsonNodeFactory
                                        .instance.textNode("2026-01-01"))),
                        null, null, null, null),
                "cloud_core_network", List.of(KB), null);
        assertThat(out.rows()).hasSize(2);
        assertThat(out.rows().stream().map(r -> r.get("告警码")))
                .containsExactlyInAnyOrder("A1-101", "A1-102");
    }

    @Test
    @DisplayName("数值过滤/聚合走规范值：千分位展示值不破坏比较")
    void numericFilterAndAggregateOnNormalizedBasis() {
        var out = queryService.query(stRef,
                new StructuredQueryService.QuerySpec(
                        null,
                        List.of(new StructuredQueryService.WhereClause(
                                "功耗", "gt",
                                com.fasterxml.jackson.databind.node.JsonNodeFactory
                                        .instance.numberNode(1000))),
                        null, null, null,
                        new StructuredQueryService.Aggregate("sum", "功耗")),
                "cloud_core_network", List.of(KB), null);
        assertThat(((Number) out.aggregate().value()).doubleValue())
                .isEqualTo(3234.5);
        assertThat(out.aggregate().row_count()).isEqualTo(2);
    }

    @Test
    @DisplayName("date 非法值与 sum/avg 拒绝：typed error，不静默退化")
    void dateGuardrails() {
        assertThatThrownBy(() -> queryService.query(stRef,
                new StructuredQueryService.QuerySpec(
                        null,
                        List.of(new StructuredQueryService.WhereClause(
                                "投产日期", "eq",
                                com.fasterxml.jackson.databind.node.JsonNodeFactory
                                        .instance.textNode("2026年1月"))),
                        null, null, null, null),
                "cloud_core_network", List.of(KB), null))
                .isInstanceOf(com.coremasterkb.serving.structure.StructureToolException.class);

        assertThatThrownBy(() -> queryService.query(stRef,
                new StructuredQueryService.QuerySpec(
                        null, null, null, null, null,
                        new StructuredQueryService.Aggregate("avg", "投产日期")),
                "cloud_core_network", List.of(KB), null))
                .isInstanceOf(com.coremasterkb.serving.structure.StructureToolException.class)
                .hasMessageContaining("日期列");
    }

    // ---------------------------------------------------------------------

    private void insertCell(int row, int col, String value, boolean data,
                            String normalized, String formula, String type) {
        jdbc.update(
                "INSERT INTO asset_table_cells (snapshot_id, table_ref, row_index, "
                        + "column_index, column_name, value, is_header, value_type, "
                        + "normalized_value, formula) VALUES (?,?,?,?,?,?,FALSE,?,?,?)",
                SNAP, TABLE_REF, row, col,
                List.of("告警码", "投产日期", "功耗").get(col),
                value, type, normalized, formula);
        // data 标记只是可读性——is_header 恒 FALSE（投影契约）
    }

    private TableAssetRow assetRow() {
        TableAssetRow a = new TableAssetRow();
        a.setSnapshotId(SNAP);
        a.setAssetRef(ASSET_REF);
        a.setAssetType("table");
        a.setTableRef(TABLE_REF);
        a.setColumnsJson("[\"告警码\",\"投产日期\",\"功耗\"]");
        a.setRowCount(3);
        a.setReadiness("ready");
        return a;
    }

    private boolean tableExists(String table) {
        Boolean present = jdbc.queryForObject(
                "SELECT to_regclass(?) IS NOT NULL", Boolean.class, table);
        return Boolean.TRUE.equals(present);
    }
}
