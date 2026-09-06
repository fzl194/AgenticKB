package com.coremasterkb.serving.infrastructure;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.*;
import org.springframework.web.client.RestTemplate;

import java.util.*;

/**
 * Client for the shared LLM service (llm_service).
 *
 * <p>Supports:
 * <ul>
 *   <li>{@code embed} — text embedding via POST /api/v1/models/embeddings</li>
 *   <li>{@code rerank} — model rerank via POST /api/v1/models/rerank</li>
 * </ul>
 *
 * <p>瘦身批次5：模板执行通道（{@code execute} / POST /api/v1/execute、启动期模板注册
 * {@code ensureTemplates*}、ServingTemplates、unwrapResponse）已删——生产零调用者，
 * serving 只消费 embed/rerank 两个直连端点。</p>
 *
 * <p>No health-check probing — availability is determined by baseUrl being configured.
 * Call failures are surfaced as exceptions and handled by callers (fallback, retry, etc.).
 */
public class LlmClient {

    private static final Logger log = LoggerFactory.getLogger(LlmClient.class);
    private static final ParameterizedTypeReference<Map<String, Object>> MAP_TYPE =
            new ParameterizedTypeReference<>() {};

    private final RestTemplate restTemplate;
    private final String baseUrl;
    private final ThreadLocal<String> domainHolder = new ThreadLocal<>();

    public LlmClient(RestTemplate restTemplate, String baseUrl) {
        this.restTemplate = restTemplate;
        this.baseUrl = baseUrl != null ? baseUrl.replaceAll("/+$", "") : null;
    }

    /** Set the knowledge domain for billing and audit in llm_service. */
    public void setKnowledgeDomain(String domain) {
        this.domainHolder.set(domain);
    }

    /** Clear the thread-local domain (call in finally block). */
    public void clearKnowledgeDomain() {
        this.domainHolder.remove();
    }

    private String getKnowledgeDomain() {
        return this.domainHolder.get();
    }

    // =========================================================================
    // Availability — lightweight check (no HTTP call)
    // =========================================================================

    /**
     * Returns true if the client has a non-blank baseUrl configured.
     * No HTTP health-check is performed — call failures are handled by callers.
     */
    public boolean isAvailable() {
        return baseUrl != null && !baseUrl.isBlank();
    }

    // =========================================================================
    // Embeddings
    // =========================================================================

    /**
     * Call the embedding endpoint.
     * Model and dimensions are managed by llm_service — callers only provide texts.
     */
    public Map<String, Object> embed(List<String> texts) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("input", texts);
        payload.put("caller_service", "serving");
        if (getKnowledgeDomain() != null && !getKnowledgeDomain().isBlank()) {
            payload.put("knowledge_domain", getKnowledgeDomain());
        }

        ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                baseUrl + "/api/v1/models/embeddings",
                HttpMethod.POST,
                new HttpEntity<>(payload, buildHeaders()),
                MAP_TYPE);
        return response.getBody() != null ? response.getBody() : Map.of();
    }

    // =========================================================================
    // Rerank
    // =========================================================================

    /**
     * Call the rerank endpoint.
     * Model is managed by llm_service — callers only provide query, documents, and optional topN.
     */
    public Map<String, Object> rerank(String query, List<String> documents, Integer topN) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("query", query);
        payload.put("documents", documents);
        if (topN != null) payload.put("top_n", topN);
        payload.put("caller_service", "serving");
        if (getKnowledgeDomain() != null && !getKnowledgeDomain().isBlank()) {
            payload.put("knowledge_domain", getKnowledgeDomain());
        }

        ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                baseUrl + "/api/v1/models/rerank",
                HttpMethod.POST,
                new HttpEntity<>(payload, buildHeaders()),
                MAP_TYPE);
        return response.getBody() != null ? response.getBody() : Map.of();
    }

    // =========================================================================
    // Internal
    // =========================================================================

    private HttpHeaders buildHeaders() {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        return headers;
    }
}
