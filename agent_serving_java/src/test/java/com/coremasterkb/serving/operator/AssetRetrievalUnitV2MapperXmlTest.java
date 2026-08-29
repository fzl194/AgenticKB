package com.coremasterkb.serving.operator;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * v2 召回 mapper 的 SQL 文本契约测试（批次8 R2）。PG 集成测试（AbstractPgIntegrationTest，
 * {@code mvn verify -Dgroups=pg-integration}）不可用时，这里锁定 SQL 关键语义：
 * eligibility 下推、'simple' + plainto_tsquery 同源契约、参数化 JSONB containment、
 * 维度一致、scope 在 LIMIT 前下推。
 */
@DisplayName("AssetRetrievalUnitV2Mapper SQL contract")
class AssetRetrievalUnitV2MapperXmlTest {

    private static String mapperXml() throws Exception {
        try (InputStream in = AssetRetrievalUnitV2MapperXmlTest.class.getClassLoader()
                .getResourceAsStream("mapper/AssetRetrievalUnitV2Mapper.xml")) {
            assertThat(in).as("mapper XML must be on classpath").isNotNull();
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    @Test
    @DisplayName("fts searches the v2 units table, lexical_eligible only, before LIMIT")
    void ftsCoreContract() throws Exception {
        String xml = mapperXml();

        assertThat(xml).contains("FROM asset_retrieval_units_v2");
        assertThat(xml).contains("lexical_eligible = TRUE");
        // 同源分词契约：'simple' 配置 + 参数化 plainto_tsquery（查询侧 jieba 预分词后喂入）
        assertThat(xml).contains("plainto_tsquery('simple', #{lexicalQuery})");
        assertThat(xml).contains("search_vector @@ plainto_tsquery");
        assertThat(xml).contains("ts_rank(search_vector");
        // scope/filters 在 Top-K 前下推：WHERE 内含 snapshot_id IN
        assertThat(xml).contains("snapshot_id IN");
    }

    @Test
    @DisplayName("hard filters push down as parameterized JSONB containment + typed IN lists")
    void filterPushdownContract() throws Exception {
        String xml = mapperXml();

        assertThat(xml).contains("facets_json @&gt; #{docParam}::jsonb");
        assertThat(xml).contains("representation_type IN");
        assertThat(xml).contains("content_type IN");
        assertThat(xml).contains("target_ref IN");
        // 参数化：不含字符串拼接的 facets 过滤（没有 ${} 插值出现在这些过滤附近）
        assertThat(xml).doesNotContain("${docParam}");
        assertThat(xml).doesNotContain("${lexicalQuery}");
    }

    @Test
    @DisplayName("dense joins v2 embeddings with units, dense_eligible + dimension match")
    void denseCoreContract() throws Exception {
        String xml = mapperXml();

        assertThat(xml).contains("FROM asset_retrieval_embeddings_v2 e");
        assertThat(xml).contains("JOIN asset_retrieval_units_v2 u");
        assertThat(xml).contains("u.dense_eligible = TRUE");
        assertThat(xml).contains("e.dimension = #{dim}");
        assertThat(xml).contains("e.embedding_vector_vec &lt;=&gt; #{queryVector}::vector");
        // 旧 unit_type / text_kind 映射不得再出现
        assertThat(xml).doesNotContain("unit_type");
        assertThat(xml).doesNotContain("text_kind");
    }

    @Test
    @DisplayName("canonical evidence id is selected for channel aggregation")
    void canonicalColumnSelected() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("canonical_evidence_id");
    }
}
