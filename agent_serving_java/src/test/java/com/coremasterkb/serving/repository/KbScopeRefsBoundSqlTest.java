package com.coremasterkb.serving.repository;

import org.apache.ibatis.builder.xml.XMLMapperBuilder;
import org.apache.ibatis.session.Configuration;
import org.junit.jupiter.api.Test;
import java.io.InputStream;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * Onenet reference scope (doc 47 §四-5): selectLatestKbSnapshots must include
 * documents referenced by any requested kb (kb_document_refs), while keeping
 * the three load-bearing guards (own-kb build decision, selection_status
 * outside the DISTINCT ON, domain guard).
 *
 * BoundSql-level regression in the style of StructureBoundSqlRegressionTest:
 * the dynamic statement is executed as text, not run against a database.
 */
class KbScopeRefsBoundSqlTest {

    private String sql(String statement, List<String> kbIds) throws Exception {
        Configuration configuration = new Configuration();
        String resource = "mapper/AssetBuildDocumentSnapshotMapper.xml";
        try (InputStream input = getClass().getClassLoader().getResourceAsStream(resource)) {
            new XMLMapperBuilder(input, configuration, resource, configuration.getSqlFragments()).parse();
        }
        Map<String, Object> parameters = new HashMap<>();
        parameters.put("domain", "cloud_core_network");
        parameters.put("kbIds", kbIds);
        return configuration
                .getMappedStatement("com.coremasterkb.serving.mapper."
                        + "AssetBuildDocumentSnapshotMapper." + statement)
                .getBoundSql(parameters).getSql().replaceAll("\\s+", " ").trim();
    }

    @Test
    void referencesAreUnionedIntoKbScope() throws Exception {
        String generated = sql("selectLatestKbSnapshots", List.of("kb-a", "kb-b"));
        // own-kb branch survives (foreach renders "? , ?" — assert structure,
        // not exact spacing)
        assertThat(generated).contains("d.kb_id IN (");
        // reference branch exists and reuses the same requested kb ids
        assertThat(generated).contains("kb_document_refs");
        assertThat(generated).contains("r.document_id = d.id");
        int refsBranchCount = generated.split("r.kb_id IN", -1).length - 1;
        assertThat(refsBranchCount).as("reference branch must re-check kb ids").isEqualTo(1);
        assertThat(generated).contains("r.kb_id IN (");
        // both branches carry the same number of placeholders (2 kb ids each)
        int placeholderCount = generated.split("\\?", -1).length - 1;
        assertThat(placeholderCount).as("domain(1) + own-branch(2) + refs-branch(2) = 5").isEqualTo(5);
    }

    @Test
    void loadBearingGuardsSurvive() throws Exception {
        String generated = sql("selectLatestKbSnapshots", List.of("kb-a"));
        // own-kb build decision
        assertThat(generated).contains("b.kb_id = d.kb_id");
        // soft-delete guard
        assertThat(generated).contains("d.deleted_at IS NULL");
        // domain guard
        assertThat(generated).contains("d.domain = ?");
        // selection_status stays OUTSIDE the DISTINCT ON
        int distinctPos = generated.indexOf("DISTINCT ON");
        int activePos = generated.indexOf("selection_status = 'active'");
        assertThat(distinctPos).isGreaterThan(0);
        assertThat(activePos).isGreaterThan(distinctPos);
    }

    @Test
    void statementStaysBalanced() throws Exception {
        String generated = sql("selectLatestKbSnapshots", List.of("kb-a", "kb-b"));
        String structural = generated.replaceAll("'([^']|'')*'", "''").replaceAll("/\\*.*?\\*/", "");
        int depth = 0;
        for (char character : structural.toCharArray()) {
            if (character == '(') depth++;
            if (character == ')') depth--;
            assertThat(depth).as("unmatched closing parenthesis in %s", generated).isGreaterThanOrEqualTo(0);
        }
        assertThat(depth).as("unclosed parenthesis in %s", generated).isZero();
    }
}
