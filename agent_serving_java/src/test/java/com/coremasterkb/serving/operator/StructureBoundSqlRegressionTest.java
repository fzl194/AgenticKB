package com.coremasterkb.serving.operator;

import org.apache.ibatis.builder.xml.XMLMapperBuilder;
import org.apache.ibatis.session.Configuration;
import org.junit.jupiter.api.Test;
import java.io.InputStream;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import static org.assertj.core.api.Assertions.assertThat;

class StructureBoundSqlRegressionTest {
    private String sql(String mapper, String statement, Map<String, Object> parameters) throws Exception {
        Configuration configuration = new Configuration();
        String resource = "mapper/" + mapper + ".xml";
        try (InputStream input = getClass().getClassLoader().getResourceAsStream(resource)) {
            new XMLMapperBuilder(input, configuration, resource, configuration.getSqlFragments()).parse();
        }
        return configuration.getMappedStatement("com.coremasterkb.serving.operator.mapper."
                + mapper + "." + statement).getBoundSql(parameters).getSql().replaceAll("\\s+", " ").trim();
    }

    @Test
    void descendantsFtsHasBalancedStatementParentheses() throws Exception { checkSearch("searchFtsV2"); }

    @Test
    void descendantsDenseHasBalancedStatementParentheses() throws Exception { checkSearch("searchDenseV2"); }

    private void checkSearch(String statement) throws Exception {
        Map<String, Object> parameters = new HashMap<>();
        parameters.put("snapshotIds", List.of("snapshot"));
        parameters.put("targetRefs", List.of("document#section:0"));
        parameters.put("sectionScopeDescendants", true);
        String generated = sql("AssetRetrievalUnitV2Mapper", statement, parameters);
        assertThat(generated).contains("WITH RECURSIVE sec(snapshot_id, ref)");
        // Validate the expanded dynamic statement, not XML substrings. Ignore SQL literals/comments.
        String structural = generated.replaceAll("'([^']|'')*'", "''").replaceAll("/\\*.*?\\*/", "");
        int depth = 0;
        for (char character : structural.toCharArray()) {
            if (character == '(') depth++;
            if (character == ')') depth--;
            assertThat(depth).as("unmatched closing parenthesis in %s", generated).isGreaterThanOrEqualTo(0);
        }
        assertThat(depth).as("unclosed parenthesis in %s", generated).isZero();
    }

    @Test
    void directoryPrefixFiltersOnDocumentDirectoryPath() throws Exception {
        // 1.1.14 内网勘误回归钉：directory_prefix 必须按 asset_documents.directory_path
        // 过滤（快照经 asset_document_snapshot_links 归属）。原 LIKE facets.document
        // 'doc:/…' 对一张网文档（document_key=onenet:{source}:{sha}，不含目录）整库
        // 零命中——免 PG 门禁只能靠展开语句的文本形态守住归属链与字面匹配（strpos）。
        Map<String, Object> parameters = new HashMap<>();
        parameters.put("snapshotIds", List.of("snapshot"));
        parameters.put("directoryPrefix", "产品文档");
        for (String statement : List.of("searchFtsV2", "searchDenseV2")) {
            String generated = sql("AssetRetrievalUnitV2Mapper", statement, parameters);
            assertThat(generated)
                    .as("%s 的 directory_prefix 必须走 directory_path 归属链", statement)
                    .contains("asset_document_snapshot_links")
                    .contains("d.directory_path = ?")
                    .contains("strpos(d.directory_path, ? || '/') = 1")
                    .doesNotContain("facets_json->>")  // 旧 facets 过滤路径必须绝迹（SELECT 列除外）
                    .doesNotContain(" LIKE ");        // 字面前缀不走通配符
        }
    }

    @Test
    void explicitRowIndexColumnOrdersByCellValue() throws Exception {
        Map<String, Object> parameters = new HashMap<>();
        parameters.put("criteria", List.of());
        parameters.put("orderField", "row_index");
        parameters.put("orderDir", "asc");
        parameters.put("numericOrder", true);
        String generated = sql("StructureToolMapper", "selectStructuredRows", parameters);
        assertThat(generated).contains("ORDER BY NULLIF(REPLACE(t.cells_norm ->> ?");
    }

    @Test
    void omittedOrderUsesPhysicalRowIndex() throws Exception {
        Map<String, Object> parameters = new HashMap<>();
        parameters.put("criteria", List.of());
        parameters.put("orderDir", "asc");
        parameters.put("numericOrder", false);
        String generated = sql("StructureToolMapper", "selectStructuredRows", parameters);
        assertThat(generated).contains("ORDER BY t.row_index ASC");
    }
}
