package com.coremasterkb.serving.operator.operators.retrieve;

import com.huaban.analysis.jieba.JiebaSegmenter;
import com.huaban.analysis.jieba.SegToken;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

/**
 * 查询侧分词器（批次8 R2，25 号 §6.4 契约）。
 *
 * <p><b>同源契约：</b>索引侧 {@code asset_retrieval_units_v2.lexical_text} 由 Python jieba
 * 预分词（{@code tokenize_for_search}，{@code tokenizer_version = jieba-default-1}，见
 * mining {@code retrieval_projection/persist.py}），PG 端 {@code to_tsvector('simple', ...)} 按空白
 * 切词。查询侧必须用<b>同族分词器</b>（Java {@code jieba-analysis}）把 query 切成空格连接的
 * token 串，再交给 {@code plainto_tsquery('simple', ?)}——两侧分词版本进日志可观测
 * （{@link #QUERY_TOKENIZER_VERSION} vs {@link #INDEX_TOKENIZER_VERSION}）。</p>
 *
 * <p>单例 {@link JiebaSegmenter}（线程安全），SEARCH 模式。非 CJK 文本天然按空白/标点透传，
 * 与 'simple' 配置的切词行为兼容。</p>
 */
public final class QueryTokenizer {

    private static final Logger log = LoggerFactory.getLogger(QueryTokenizer.class);

    /** 查询侧分词实现版本（进日志/trace，与索引侧版本对照）。 */
    public static final String QUERY_TOKENIZER_VERSION = "jieba-analysis-1.0.2-search";

    /** 索引侧 lexical_text 的分词版本（mining TOKENIZER_VERSION 冻结值）。 */
    public static final String INDEX_TOKENIZER_VERSION = "jieba-default-1";

    private static final JiebaSegmenter SEGMENTER = new JiebaSegmenter();

    private QueryTokenizer() {}

    /** SEARCH 模式分词；空白与纯标点 token 被丢弃（PG 'simple' 解析器两侧同样丢弃），纯空白输入返回空列表。 */
    public static List<String> tokenize(String text) {
        if (text == null || text.isBlank()) {
            return List.of();
        }
        List<String> tokens = new ArrayList<>();
        for (SegToken t : SEGMENTER.process(text, JiebaSegmenter.SegMode.SEARCH)) {
            String word = t.word == null ? "" : t.word.trim();
            if (word.isEmpty() || isPunctuationOnly(word)) {
                continue;
            }
            tokens.add(word);
        }
        return tokens;
    }

    /** 纯标点/符号 token（无字母、数字、CJK）——索引侧 to_tsvector 与查询侧 plainto_tsquery 都会丢弃。 */
    private static boolean isPunctuationOnly(String word) {
        return word.codePoints().noneMatch(Character::isLetterOrDigit);
    }

    /**
     * 分词后空格 join——直接可作 {@code plainto_tsquery('simple', ?)} 的参数。
     * 空白结果返回 {@code ""}（调用方按"空结果是正常结果"处理，不构造查询）。
     */
    public static String toLexicalQuery(String text) {
        List<String> tokens = tokenize(text);
        if (tokens.isEmpty()) {
            log.debug("[query_tokenizer] no tokens after segmentation (index={} query={})",
                    INDEX_TOKENIZER_VERSION, QUERY_TOKENIZER_VERSION);
            return "";
        }
        return String.join(" ", tokens);
    }
}
