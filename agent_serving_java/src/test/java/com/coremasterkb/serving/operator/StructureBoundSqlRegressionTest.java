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
