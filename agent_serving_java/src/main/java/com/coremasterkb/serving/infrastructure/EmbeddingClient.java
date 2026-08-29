package com.coremasterkb.serving.infrastructure;

import java.util.*;

/**
 * Client for text embedding via llm_service.
 *
 * <p>Model and dimensions are managed by llm_service — this client only sends
 * the text input and relies on llm_service defaults.
 */
public class EmbeddingClient {

    private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(EmbeddingClient.class);
    private final LlmClient llmClient;

    public EmbeddingClient(LlmClient llmClient) {
        this.llmClient = llmClient;
    }

    public boolean isConfigured() {
        return llmClient.isAvailable();
    }

    @SuppressWarnings("unchecked")
    public float[] embed(String text) {
        Map<String, Object> response = llmClient.embed(List.of(text));
        List<Map<String, Object>> data = (List<Map<String, Object>>) response.get("data");
        if (data != null && !data.isEmpty()) {
            List<Number> embedding = (List<Number>) data.get(0).get("embedding");
            if (embedding != null) {
                float[] result = new float[embedding.size()];
                for (int i = 0; i < embedding.size(); i++) {
                    result[i] = embedding.get(i).floatValue();
                }
                return result;
            }
        }
        return null;
    }

    /**
     * Batch embed multiple texts in a single API call.
     * Returns a list of float arrays, one per input text, in order.
     * Falls back to per-text embedding on batch failure.
     */
    @SuppressWarnings("unchecked")
    public List<float[]> embedBatch(List<String> texts) {
        if (texts == null || texts.isEmpty()) return List.of();
        if (texts.size() == 1) return List.of(embed(texts.get(0)));
        try {
            Map<String, Object> response = llmClient.embed(texts);
            List<Map<String, Object>> data = (List<Map<String, Object>>) response.get("data");
            if (data != null && data.size() == texts.size()) {
                List<float[]> results = new ArrayList<>();
                for (Map<String, Object> item : data) {
                    List<Number> embedding = (List<Number>) item.get("embedding");
                    if (embedding != null) {
                        float[] vec = new float[embedding.size()];
                        for (int i = 0; i < embedding.size(); i++) {
                            vec[i] = embedding.get(i).floatValue();
                        }
                        results.add(vec);
                    } else {
                        results.add(null);
                    }
                }
                return results;
            }
        } catch (Exception e) {
            log.warn("Batch embedding failed, falling back to per-text embedding: {}", e.getMessage());
        }
        // Fallback: embed one by one
        return texts.stream().map(t -> {
            try { return embed(t); } catch (Exception e) { return null; }
        }).toList();
    }

}
