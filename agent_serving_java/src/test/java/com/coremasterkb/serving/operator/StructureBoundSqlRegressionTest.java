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
    void directoryPrefixUsesTextArrowNotJsonb() throws Exception {
        // codex P1 回归钉：LIKE 左侧必须是 text（->>）——单箭头返回 jsonb，
        // PG 无 jsonb~~text 运算符（42883），且 SKIP_WITH_EMPTY 会把异常静默成空结果。
        // 免 PG 的门禁只能靠展开语句的文本形态守住箭头位数。
        Map<String, Object> parameters = new HashMap<>();
        parameters.put("snapshotIds", List.of("snapshot"));
        parameters.put("directoryPrefix", "产品文档");
        for (String statement : List.of("searchFtsV2", "searchDenseV2")) {
            String generated = sql("AssetRetrievalUnitV2Mapper", statement, parameters);
            assertThat(generated)
                    .as("%s 的 directory_prefix 断言必须用 ->>'document'", statement)
                    .contains("facets_json->>'document' LIKE")
                    .doesNotContain("->'document'");  // 单箭头（jsonb）形式
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
