package com.coremasterkb.serving.integration;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.*;
import org.springframework.web.client.RestTemplate;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration tests that verify the actual llm_service contract.
 * These tests require llm_service running at http://localhost:8900.
 *
 * <p>瘦身批次5：模板执行通道的三个 /api/v1/execute 诊断测试已随 LlmClient.execute()
 * 退役删除——serving 只消费 embed/rerank 两个直连端点，此处保留对它们的真机契约验证。</p>
 */
@Tag("pg-integration")
@DisplayName("LLM Service Integration")
class LlmServiceIntegrationTest {

    private static final String BASE_URL = "http://localhost:8900";
    private static RestTemplate restTemplate;

    @BeforeAll
    static void setUp() {
        restTemplate = new RestTemplate();
    }

    /**
     * Check if llm_service is reachable. Tests are skipped if not.
     */
    static boolean isLlmServiceAvailable() {
        try {
            ResponseEntity<Map<String, Object>> resp = restTemplate.exchange(
                    BASE_URL + "/health", HttpMethod.GET, null,
                    new ParameterizedTypeReference<>() {});
            return resp.getStatusCode().is2xxSuccessful();
        } catch (Exception e) {
            return false;
        }
    }

    @Test
    @EnabledIf("isLlmServiceAvailable")
    @DisplayName("POST /api/v1/models/embeddings returns valid embeddings")
    void embeddings_returnsValidResult() {
        Map<String, Object> payload = Map.of(
                "input", List.of("SMF是什么网元")
        );

        ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                BASE_URL + "/api/v1/models/embeddings",
                HttpMethod.POST,
                new HttpEntity<>(payload, jsonHeaders()),
                new ParameterizedTypeReference<>() {});

        assertThat(response.getStatusCode().is2xxSuccessful()).isTrue();
        Map<String, Object> body = response.getBody();
        assertThat(body).containsKey("data");

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> data = (List<Map<String, Object>>) body.get("data");
        assertThat(data).hasSize(1);

        @SuppressWarnings("unchecked")
        List<Number> embedding = (List<Number>) data.get(0).get("embedding");
        assertThat(embedding).isNotEmpty();

        System.out.println("Embedding OK: dim=" + embedding.size());
    }

    @Test
    @EnabledIf("isLlmServiceAvailable")
    @DisplayName("POST /api/v1/models/rerank returns ranked results")
    void rerank_returnsRankedResults() {
        Map<String, Object> payload = Map.of(
                "query", "SMF是什么",
                "documents", List.of(
                        "SMF是会话管理功能，负责PDU会话管理",
                        "UPF是用户面功能，负责数据转发",
                        "AMF是接入和移动性管理功能"
                ),
                "top_n", 3
        );

        ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                BASE_URL + "/api/v1/models/rerank",
                HttpMethod.POST,
                new HttpEntity<>(payload, jsonHeaders()),
                new ParameterizedTypeReference<>() {});

        assertThat(response.getStatusCode().is2xxSuccessful()).isTrue();
        Map<String, Object> body = response.getBody();
        assertThat(body).containsKey("results");

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> results = (List<Map<String, Object>>) body.get("results");
        assertThat(results).hasSize(3);

        // SMF should be ranked first
        assertThat(results.get(0).get("index")).isEqualTo(0);
        double topScore = ((Number) results.get(0).get("relevance_score")).doubleValue();
        assertThat(topScore).isGreaterThan(0.5);

        System.out.println("Rerank OK: top_score=" + topScore);
        results.forEach(r -> System.out.println("  idx=" + r.get("index") + " score=" + r.get("relevance_score")));
    }

    // =========================================================================
    // Helpers
    // =========================================================================

    private static HttpHeaders jsonHeaders() {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        return headers;
    }
}
