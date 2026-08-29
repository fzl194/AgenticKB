package com.coremasterkb.serving.operator.operators.retrieve;

import com.coremasterkb.serving.domain.ActiveScope;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 显式 hard filters → Top-K 前下推参数的确定性映射（25 号 §6.2/§7.1）。只有显式传入的键
 * 生成约束；未知键忽略；无 filters = 空约束（宽检索）。
 */
@DisplayName("ScopeFilterPushdown")
class ScopeFilterPushdownTest {

    @Test
    @DisplayName("no filters → empty pushdown")
    void noneWhenNoFilters() {
        ActiveScope scope = new ActiveScope("rel", "b1", List.of("s1"), Map.of());

        ScopeFilterPushdown p = ScopeFilterPushdown.from(scope);

        assertThat(p.isEmpty()).isTrue();
        assertThat(p.documentJsonParams()).isEmpty();
        assertThat(p.representationTypes()).isEmpty();
    }

    @Test
    @DisplayName("null scope → empty pushdown")
    void nullScopeIsEmpty() {
        assertThat(ScopeFilterPushdown.from(null).isEmpty()).isTrue();
    }

    @Test
    @DisplayName("document_refs → parameterized facets_json @> JSONB params")
    void documentRefsBecomeJsonbParams() {
        var p = ScopeFilterPushdown.fromFilters(Map.of(
                "document_refs", List.of("doc-1", "doc-2")));

        assertThat(p.documentJsonParams()).containsExactly(
                "{\"document\":\"doc-1\"}", "{\"document\":\"doc-2\"}");
        assertThat(p.isEmpty()).isFalse();
    }

    @Test
    @DisplayName("evidence_types → representation types; asset_types → content types")
    void typedFiltersSplit() {
        var p = ScopeFilterPushdown.fromFilters(Map.of(
                "evidence_types", List.of("table_row", "prose"),
                "asset_types", List.of("table")));

        assertThat(p.representationTypes()).containsExactly("table_row", "prose");
        assertThat(p.contentTypes()).containsExactly("table");
    }

    @Test
    @DisplayName("section_refs → target refs (within declaration)")
    void sectionRefsBecomeTargetRefs() {
        var p = ScopeFilterPushdown.fromFilters(Map.of(
                "section_refs", List.of("doc-1#section:概述")));

        assertThat(p.targetRefs()).containsExactly("doc-1#section:概述");
    }

    @Test
    @DisplayName("unknown keys (relative_path_prefix/date_range) are ignored here, not guessed")
    void unknownKeysIgnored() {
        var p = ScopeFilterPushdown.fromFilters(Map.of(
                "relative_path_prefix", "规范/接入网",
                "date_range", Map.of("from", "2026-01-01", "to", "2026-12-31")));

        assertThat(p.isEmpty()).isTrue();
    }

    @Test
    @DisplayName("JSON escaping keeps containment parameterized and safe")
    void jsonEscaping() {
        assertThat(ScopeFilterPushdown.jsonQuote("a\"b\\c")).isEqualTo("\"a\\\"b\\\\c\"");
    }

    @Test
    @DisplayName("blank entries are dropped, non-list values ignored")
    void blankAndNonListDropped() {
        var p = ScopeFilterPushdown.fromFilters(Map.of(
                "document_refs", List.of("doc-1", "  "),
                "evidence_types", "prose"));

        assertThat(p.documentJsonParams()).containsExactly("{\"document\":\"doc-1\"}");
        assertThat(p.representationTypes()).isEmpty();
    }
}
