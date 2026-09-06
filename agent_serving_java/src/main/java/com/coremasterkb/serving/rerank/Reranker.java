package com.coremasterkb.serving.rerank;

import com.coremasterkb.serving.domain.RetrievalCandidate;

import java.util.List;

/**
 * Reranker interface for post-retrieval candidate reordering.
 *
 * <p>Implementations may return {@code null} to signal failure,
 * allowing the pipeline to fall back to the next strategy.
 *
 * <p>瘦身批次5：签名从 {@code rerank(candidates, QueryUnderstanding)} 收敛为
 * {@code rerank(query, candidates)}——历史实现只取 understanding 的 originalQuery
 * 一个字段，调用方为凑参数手工伪造整个对象。</p>
 */
public interface Reranker {

    /**
     * Rerank candidates for the given query.
     *
     * @param query      the user query
     * @param candidates input candidates (may be in any order)
     * @return reranked candidates, or {@code null} if this reranker cannot produce a result
     */
    List<RetrievalCandidate> rerank(String query, List<RetrievalCandidate> candidates);
}
