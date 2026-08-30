package com.coremasterkb.serving.operator;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 批次8 R5 hydrate mapper 的 SQL 文本契约测试。PG 集成测试不可用时锁定关键语义：
 * 批量 IN 下推（禁 N+1/全库扫描）、章节/文档/表格 row_number 分组截断、
 * returnable 过滤实现 alias 回源、参数化（无 ${} 插值）。
 */
@DisplayName("EvidenceSourceV2Mapper SQL contract")
class EvidenceSourceV2MapperXmlTest {

    private static String mapperXml() throws Exception {
        try (InputStream in = EvidenceSourceV2MapperXmlTest.class.getClassLoader()
                .getResourceAsStream("mapper/EvidenceSourceV2Mapper.xml")) {
            assertThat(in).as("mapper XML must be on classpath").isNotNull();
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    @Test
    @DisplayName("canonical lookup filters returnable=TRUE — alias rows never come back")
    void aliasExcludedByReturnable() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("returnable = TRUE");
        assertThat(xml).contains("canonical_evidence_id IN");
        assertThat(xml).contains("snapshot_id IN");
    }

    @Test
    @DisplayName("window expansion is bounded per anchor (BETWEEN on ordinal, no full-section scan)")
    void windowBoundedPerAnchor() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("n.ordinal BETWEEN #{a.centerOrdinal} - #{a.radius} AND #{a.centerOrdinal} + #{a.radius}");
        assertThat(xml).contains("n.parent_ref = #{a.parentRef}");
        // 一次批量查询覆盖全部锚点（OR-per-anchor），不是逐候选查询
        assertThat(xml).contains("<foreach collection=\"anchors\" item=\"a\" separator=\" OR \">");
    }

    @Test
    @DisplayName("section/document/cell expansions are bounded by row_number partition caps")
    void boundedByRowNumber() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("row_number() OVER (PARTITION BY n.snapshot_id, n.parent_ref ORDER BY n.ordinal)");
        assertThat(xml).contains("row_number() OVER (PARTITION BY n.snapshot_id ORDER BY n.ordinal)");
        assertThat(xml).contains("row_number() OVER (PARTITION BY c.snapshot_id, c.table_ref");
        assertThat(xml).contains("t.rn &lt;= #{maxRowsPerSection}");
        assertThat(xml).contains("t.rn &lt;= #{maxRowsPerDocument}");
        assertThat(xml).contains("t.rn &lt;= #{maxCellsPerTable}");
    }

    @Test
    @DisplayName("document tokens aggregate per snapshot (whole-document fits decision)")
    void tokenTotalsGroupedBySnapshot() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("SUM(COALESCE(token_count, 0))");
        // 2de813a：raw 表实际为 001 DDL 的 legacy 形态（document_snapshot_id 列）
        assertThat(xml).contains("GROUP BY document_snapshot_id");
    }

    @Test
    @DisplayName("table header prefers structured asset columns; cells ordered header-first")
    void tableContracts() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("FROM asset_structured_assets");
        assertThat(xml).contains("FROM asset_table_cells");
        assertThat(xml).contains("ORDER BY c.is_header DESC, c.row_index, c.column_index");
    }

    @Test
    @DisplayName("source projection resolves via snapshot link (document_key is not globally unique)")
    void sourceProjectionBySnapshot() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("FROM asset_document_snapshot_links l");
        assertThat(xml).contains("l.document_snapshot_id IN");
        assertThat(xml).contains("d.deleted_at IS NULL");
        assertThat(xml).contains("LEFT JOIN knowledge_bases k");
    }

    @Test
    @DisplayName("fully parameterized — no ${} interpolation anywhere")
    void noStringInterpolation() throws Exception {
        assertThat(mapperXml()).doesNotContain("${");
    }
}
