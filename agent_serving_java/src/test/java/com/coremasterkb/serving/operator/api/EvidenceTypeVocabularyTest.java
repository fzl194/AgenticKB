package com.coremasterkb.serving.operator.api;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * A0-4（34 号 §P0 系）：对外 evidence type 统一词表。evidence[].type 输出的公开词必须
 * 能原样放回 evidence_types filter——公开词与内部 representation_type 的映射只发生在
 * 服务边界（{@link EvidenceTypeVocabulary}），单一真相源，不在两处各写一份。
 */
@DisplayName("A0-4 EvidenceTypeVocabulary 公开/内部类型映射")
class EvidenceTypeVocabularyTest {

    @Test
    @DisplayName("公开词表 = 九词（prose/section/document/table/table_row/list/code/formula/figure_caption）")
    void publicVocabularyIsTheNineWords() {
        assertThat(EvidenceTypeVocabulary.PUBLIC_TYPES).containsExactly(
                "prose", "section", "document", "table", "table_row",
                "list", "code", "formula", "figure_caption");
    }

    @Test
    @DisplayName("内部词 → 公开词：code_block→code、list_group→list；其余恒等")
    void internalToPublic() {
        assertThat(EvidenceTypeVocabulary.toPublicType("code_block")).isEqualTo("code");
        assertThat(EvidenceTypeVocabulary.toPublicType("list_group")).isEqualTo("list");
        assertThat(EvidenceTypeVocabulary.toPublicType("prose")).isEqualTo("prose");
        assertThat(EvidenceTypeVocabulary.toPublicType("table_row")).isEqualTo("table_row");
        assertThat(EvidenceTypeVocabulary.toPublicType("section")).isEqualTo("section");
        assertThat(EvidenceTypeVocabulary.toPublicType("document")).isEqualTo("document");
        assertThat(EvidenceTypeVocabulary.toPublicType("table")).isEqualTo("table");
        assertThat(EvidenceTypeVocabulary.toPublicType("formula")).isEqualTo("formula");
        assertThat(EvidenceTypeVocabulary.toPublicType("figure_caption")).isEqualTo("figure_caption");
        // alias 不进入对外 evidence type 面（hydrate 输出按 target 事实兜底，不透出内部词）
        assertThat(EvidenceTypeVocabulary.toPublicType("query_alias")).isNull();
        assertThat(EvidenceTypeVocabulary.toPublicType("summary_alias")).isNull();
        assertThat(EvidenceTypeVocabulary.toPublicType("segment")).isNull();
    }

    @Test
    @DisplayName("公开词 → 内部词：list→list_group、code→code_block；内部别名兼容；未知 → null")
    void publicToInternal() {
        assertThat(EvidenceTypeVocabulary.toRepresentationType("list")).isEqualTo("list_group");
        assertThat(EvidenceTypeVocabulary.toRepresentationType("code")).isEqualTo("code_block");
        assertThat(EvidenceTypeVocabulary.toRepresentationType("prose")).isEqualTo("prose");
        // 历史内部词直接兼容（不破旧调用方）
        assertThat(EvidenceTypeVocabulary.toRepresentationType("list_group")).isEqualTo("list_group");
        assertThat(EvidenceTypeVocabulary.toRepresentationType("code_block")).isEqualTo("code_block");
        // alias 不在对外证据类型面（只助召回，不作为证据）
        assertThat(EvidenceTypeVocabulary.toRepresentationType("query_alias")).isNull();
        assertThat(EvidenceTypeVocabulary.toRepresentationType("summary_alias")).isNull();
        assertThat(EvidenceTypeVocabulary.toRepresentationType("vector")).isNull();
        assertThat(EvidenceTypeVocabulary.toRepresentationType("")).isNull();
        assertThat(EvidenceTypeVocabulary.toRepresentationType(null)).isNull();
    }

    @Test
    @DisplayName("九公开词全部能映射为有效 representation_type（evidence[].type 可原样回传 filter）")
    void everyPublicTypeMapsToRepresentation() {
        for (String t : EvidenceTypeVocabulary.PUBLIC_TYPES) {
            assertThat(EvidenceTypeVocabulary.toRepresentationType(t))
                    .as("public type %s must map", t)
                    .isNotNull();
        }
    }

    @Test
    @DisplayName("公开词与内部词往返一致（representation → public → representation）")
    void roundTripStable() {
        List<String> internalTypes = List.of(
                "prose", "section", "document", "table", "table_row",
                "list_group", "code_block", "formula", "figure_caption");
        for (String rep : internalTypes) {
            assertThat(EvidenceTypeVocabulary.toRepresentationType(
                    EvidenceTypeVocabulary.toPublicType(rep)))
                    .as("round trip of %s", rep)
                    .isEqualTo(rep);
        }
    }
}
