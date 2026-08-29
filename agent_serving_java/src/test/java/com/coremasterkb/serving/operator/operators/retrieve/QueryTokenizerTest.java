package com.coremasterkb.serving.operator.operators.retrieve;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 查询侧分词契约（25 号 §6.4）：CJK 经 jieba SEARCH 模式分词、空格 join 后可直接作
 * plainto_tsquery('simple', ?) 输入；拉丁词原样保留；空白输入 = 空结果（正常）。
 */
@DisplayName("QueryTokenizer")
class QueryTokenizerTest {

    @Test
    @DisplayName("CJK query is segmented into space-joined tokens")
    void cjkSegmented() {
        String lexical = QueryTokenizer.toLexicalQuery("接入网传输设备功耗");

        assertThat(lexical).isNotBlank();
        assertThat(lexical).doesNotContain("接入网传输设备功耗");
        // 每个分词片段都必须能在原句中找到（同族分词，不发明新词）。
        for (String token : lexical.split(" ")) {
            assertThat("接入网传输设备功耗").contains(token);
        }
    }

    @Test
    @DisplayName("latin words survive as tokens (lower-cased like the 'simple' tsvector config); punctuation dropped")
    void latinPreserved() {
        List<String> tokens = QueryTokenizer.tokenize("OLT & GPON, port status");

        // jieba-analysis lower-cases latin; PG 'simple' config lower-cases too — 两侧一致
        assertThat(tokens).contains("olt", "gpon", "port", "status");
        assertThat(tokens).noneMatch(t -> t.contains(",") || t.contains("&"));
    }

    @Test
    @DisplayName("mixed CJK + latin query yields both kinds of tokens")
    void mixedQuery() {
        List<String> tokens = QueryTokenizer.tokenize("GPON 光模块接收功率阈值");

        assertThat(tokens).contains("gpon");
        assertThat(tokens.stream().anyMatch(t -> t.codePoints()
                .anyMatch(cp -> cp >= 0x4e00 && cp <= 0x9fff))).isTrue();
    }

    @Test
    @DisplayName("blank input → empty token list and empty lexical query")
    void blankIsEmpty() {
        assertThat(QueryTokenizer.tokenize(null)).isEmpty();
        assertThat(QueryTokenizer.tokenize("   ")).isEmpty();
        assertThat(QueryTokenizer.toLexicalQuery("  ")).isEmpty();
    }

    @Test
    @DisplayName("tokenizer versions are declared for cross-side observability")
    void versionsDeclared() {
        assertThat(QueryTokenizer.INDEX_TOKENIZER_VERSION).isEqualTo("jieba-default-1");
        assertThat(QueryTokenizer.QUERY_TOKENIZER_VERSION).contains("jieba-analysis");
    }
}
