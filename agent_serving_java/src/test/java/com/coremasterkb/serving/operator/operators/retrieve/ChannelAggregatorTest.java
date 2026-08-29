package com.coremasterkb.serving.operator.operators.retrieve;

import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 通道内 canonical 聚合契约（25 号 §5.1/§6.4/§6.5）：同 canonical 多表示只占一个名次、
 * 保留命中 representation refs、ranking_text 有界构造。
 */
@DisplayName("ChannelAggregator")
class ChannelAggregatorTest {

    private static UnitV2Row row(String repId, String canonical, double score,
                                 String repType, String context, String content) {
        UnitV2Row r = new UnitV2Row();
        r.setRepresentationId(repId);
        r.setSnapshotId("snap-1");
        r.setCanonicalEvidenceId(canonical);
        r.setRepresentationType(repType);
        r.setTargetType("segment");
        r.setTargetRef(canonical);
        r.setStructuralContext(context);
        r.setContentText(content);
        r.setFacetsJson("{\"document\":\"doc-1\"}");
        r.setChannelScore(score);
        return r;
    }

    @Test
    @DisplayName("same canonical (raw + aliases) keeps best rank only, collects hit rep refs")
    void sameCanonicalAggregatesToOneSlot() {
        List<UnitV2Row> rows = List.of(
                row("rep-a", "ev-1", 0.9, "prose", "概述", "正文A"),
                row("rep-b", "ev-1", 0.8, "query_alias", "概述", "问题别名"),
                row("rep-c", "ev-2", 0.7, "table_row", "表头", "行内容"));

        List<RetrievalCandidate> out = ChannelAggregator.aggregate(rows, "fts", 10);

        assertThat(out).hasSize(2);
        assertThat(out.get(0).canonicalEvidenceId()).isEqualTo("ev-1");
        assertThat(out.get(0).retrievalUnitId()).isEqualTo("rep-a"); // best hit wins
        assertThat(out.get(0).representationRefs()).containsExactly("rep-a", "rep-b");
        assertThat(out.get(0).channelRank()).isEqualTo(1);
        assertThat(out.get(0).channelScore()).isEqualTo(0.9);
        assertThat(out.get(1).canonicalEvidenceId()).isEqualTo("ev-2");
        assertThat(out.get(1).channelRank()).isEqualTo(2); // ev-1's alias did not take a slot
    }

    @Test
    @DisplayName("topK truncates after canonical aggregation, not before")
    void topKTruncatesAfterAggregation() {
        List<UnitV2Row> rows = List.of(
                row("rep-a", "ev-1", 0.9, "prose", "", "a"),
                row("rep-b", "ev-2", 0.8, "prose", "", "b"),
                row("rep-c", "ev-1", 0.7, "query_alias", "", "c"),
                row("rep-d", "ev-3", 0.6, "prose", "", "d"));

        List<RetrievalCandidate> out = ChannelAggregator.aggregate(rows, "dense", 2);

        assertThat(out).hasSize(2);
        assertThat(out).extracting(RetrievalCandidate::canonicalEvidenceId)
                .containsExactly("ev-1", "ev-2");
    }

    @Test
    @DisplayName("ranking_text = structural_context + content_text, bounded at 2048 chars")
    void rankingTextBounded() {
        assertThat(ChannelAggregator.rankingText("概述 > 指标", "正文内容"))
                .isEqualTo("概述 > 指标\n正文内容");
        assertThat(ChannelAggregator.rankingText(null, "正文")).isEqualTo("正文");
        assertThat(ChannelAggregator.rankingText("上下文", null)).isEqualTo("上下文");
        assertThat(ChannelAggregator.rankingText("", "  ")).isEmpty();

        String longContext = "标".repeat(3000);
        assertThat(ChannelAggregator.rankingText(longContext, "正文"))
                .hasSize(ChannelAggregator.RANKING_TEXT_MAX_CHARS);
    }

    @Test
    @DisplayName("table_row uses header context + row content (same typed formula)")
    void tableRowRankingText() {
        var out = ChannelAggregator.aggregate(
                List.of(row("rep-r", "ev-r", 0.5, "table_row", "型号|功耗", "X1|30W")),
                "fts", 10);

        assertThat(out.get(0).rankingText()).isEqualTo("型号|功耗\nX1|30W");
        assertThat(out.get(0).representationType()).isEqualTo("table_row");
    }

    @Test
    @DisplayName("candidate carries §5.1 contract fields (target/facets/channel)")
    void contractFieldsCarried() {
        var out = ChannelAggregator.aggregate(
                List.of(row("rep-a", "ev-1", 0.9, "prose", "c", "t")), "dense", 10);

        var c = out.get(0);
        assertThat(c.channelId()).isEqualTo("dense");
        assertThat(c.source()).isEqualTo("dense");
        assertThat(c.targetType()).isEqualTo("segment");
        assertThat(c.targetRef()).isEqualTo("ev-1");
        assertThat(c.facets()).containsEntry("document", "doc-1");
        assertThat(c.scoreChain().routeSources()).containsExactly("dense");
    }

    @Test
    @DisplayName("empty rows and non-positive topK are normal empty results")
    void emptyInputs() {
        assertThat(ChannelAggregator.aggregate(List.of(), "fts", 10)).isEmpty();
        assertThat(ChannelAggregator.aggregate(null, "fts", 10)).isEmpty();
        assertThat(ChannelAggregator.aggregate(
                List.of(row("rep-a", "ev-1", 0.9, "prose", "", "x")), "fts", 0)).isEmpty();
    }
}
