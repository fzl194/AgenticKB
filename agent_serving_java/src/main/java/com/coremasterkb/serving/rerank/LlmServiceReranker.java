package com.coremasterkb.serving.rerank;

import com.coremasterkb.serving.domain.QueryUnderstanding;
import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.infrastructure.LlmClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.*;

/**
 * Model reranker backed by llm_service {@code /api/v1/models/rerank}.
 *
 * <p>Sends query + candidate documents to the shared LLM service rerank endpoint,
 * returns candidates reordered by relevance score.
 * Model is managed by llm_service — callers only provide query, documents, and top_n.
 * Returns {@code null} on any failure to signal fallback.
 *
 * @see LlmClient#rerank(String, List, Integer)
 */
public class LlmServiceReranker implements Reranker {

    private static final Logger log = LoggerFactory.getLogger(LlmServiceReranker.class);
    private static final int MAX_RERANK_DOCS = 200;

    private final LlmClient llmClient;
    private final int topN;

    public LlmServiceReranker(LlmClient llmClient) {
        this(llmClient, MAX_RERANK_DOCS);
    }

    public LlmServiceReranker(LlmClient llmClient, int topN) {
        this.llmClient = llmClient;
        this.topN = topN;
    }

    @Override
    public List<RetrievalCandidate> rerank(List<RetrievalCandidate> candidates, QueryUnderstanding understanding) {
        if (candidates == null || candidates.isEmpty()) {
            return null;
        }

        String query = resolveQuery(understanding);
        if (query == null || query.isBlank()) {
            return null;
        }

        int workingSize = Math.min(topN, candidates.size());
        List<RetrievalCandidate> workingSet = candidates.subList(0, workingSize);

        return rerankBatched(query, workingSet, candidates, workingSize);
    }

    private List<RetrievalCandidate> rerankBatched(
            String query,
            List<RetrievalCandidate> workingSet,
            List<RetrievalCandidate> allCandidates,
            int workingSize) {

        if (workingSet.size() <= MAX_RERANK_DOCS) {
            List<RetrievalCandidate> result = rerankSingle(query, workingSet);
            if (result == null) return null;
            return appendRemaining(result, allCandidates, workingSize);
        }

        // Batched rerank
        List<Map.Entry<Double, RetrievalCandidate>> scored = new ArrayList<>();
        for (int i = 0; i < workingSet.size(); i += MAX_RERANK_DOCS) {
            int end = Math.min(i + MAX_RERANK_DOCS, workingSet.size());
            List<RetrievalCandidate> batch = workingSet.subList(i, end);
            List<RetrievalCandidate> batchResult = rerankSingle(query, batch);
            if (batchResult == null) return null;
            for (var c : batchResult) {
                scored.add(Map.entry(c.score(), c));
            }
        }

        scored.sort(Map.Entry.<Double, RetrievalCandidate>comparingByKey().reversed());
        List<RetrievalCandidate> reordered = new ArrayList<>();
        for (var e : scored) {
            reordered.add(e.getValue());
        }
        return appendRemaining(reordered, allCandidates, workingSize);
    }

    @SuppressWarnings("unchecked")
    private List<RetrievalCandidate> rerankSingle(String query, List<RetrievalCandidate> candidates) {
        // Skip candidates whose text/title are both blank: llm_service rejects the whole batch
        // if any document is an empty string (422). docIndex maps each sent document back to its
        // candidate index so the API's returned indices resolve to the right candidates.
        List<String> documents = new ArrayList<>();
        List<Integer> docIndex = new ArrayList<>();
        for (int i = 0; i < candidates.size(); i++) {
            String doc = buildDocument(candidates.get(i));
            if (!doc.isBlank()) {
                documents.add(doc);
                docIndex.add(i);
            }
        }
        if (documents.isEmpty()) return null;  // nothing rerankable → signal fallback

        Map<String, Object> response;
        try {
            response = llmClient.rerank(query, documents, documents.size());
        } catch (Exception e) {
            log.warn("LLM service rerank call failed: {}", e.getMessage());
            return null;
        }

        if (response == null) return null;

        Object resultsRaw = response.get("results");
        if (!(resultsRaw instanceof List<?> rawList) || rawList.isEmpty()) return null;

        Set<Integer> seenIndices = new HashSet<>();
        List<RetrievalCandidate> reordered = new ArrayList<>();

        for (Object item : rawList) {
            if (!(item instanceof Map<?, ?> m)) continue;
            Map<String, Object> entry = (Map<String, Object>) m;

            Object idxObj = entry.get("index");
            Object scoreObj = entry.get("relevance_score");
            if (!(idxObj instanceof Number) || !(scoreObj instanceof Number)) continue;

            int docIdx = ((Number) idxObj).intValue();
            if (docIdx < 0 || docIdx >= docIndex.size()) continue;
            int idx = docIndex.get(docIdx);  // map document position back to candidate index
            if (seenIndices.contains(idx)) continue;

            seenIndices.add(idx);
            double score = ((Number) scoreObj).doubleValue();
            reordered.add(candidates.get(idx).withScore(score));
        }

        // Append candidates not returned by API
        for (int i = 0; i < candidates.size(); i++) {
            if (!seenIndices.contains(i)) {
                reordered.add(candidates.get(i));
            }
        }

        return reordered.isEmpty() ? null : reordered;
    }

    private List<RetrievalCandidate> appendRemaining(
            List<RetrievalCandidate> reordered,
            List<RetrievalCandidate> allCandidates,
            int workingSize) {
        if (workingSize < allCandidates.size()) {
            reordered.addAll(allCandidates.subList(workingSize, allCandidates.size()));
        }
        return reordered;
    }

    /**
     * Build the rerank document for one candidate. 批次8 R4（25 号 §6.7）：优先用候选的
     * {@code rankingText}（类型化有界构造，R2 产出）；旧候选（无 rankingText）沿用
     * {@code "title: text"} 元数据拼装。Returns "" when nothing is present — callers skip such
     * documents (llm_service rejects empty strings). No per-document truncation here:
     * {@code rankingText} is already bounded (2048 chars) at construction; the legacy path relies
     * on llm_service batching by total length budget.
     */
    private String buildDocument(RetrievalCandidate c) {
        String rankingText = c.rankingText();
        if (rankingText != null && !rankingText.isBlank()) {
            return rankingText;
        }
        String text = stringFromMetadata(c, "text");
        String title = stringFromMetadata(c, "title");
        if (text != null && !text.isEmpty()) {
            return (title != null && !title.isEmpty()) ? title + ": " + text : text;
        }
        return (title != null && !title.isEmpty()) ? title : "";
    }

    private String resolveQuery(QueryUnderstanding understanding) {
        if (understanding != null && understanding.originalQuery() != null
                && !understanding.originalQuery().isBlank()) {
            return understanding.originalQuery();
        }
        return null;
    }

    private static String stringFromMetadata(RetrievalCandidate c, String key) {
        Object val = c.metadata().get(key);
        return val instanceof String s ? s : "";
    }
}
