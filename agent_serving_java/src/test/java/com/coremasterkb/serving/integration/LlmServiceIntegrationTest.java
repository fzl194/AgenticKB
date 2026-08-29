package com.coremasterkb.serving.integration;

import com.coremasterkb.serving.infrastructure.EmbeddingClient;
import com.coremasterkb.serving.infrastructure.LlmClient;
import com.coremasterkb.serving.rerank.LlmServiceReranker;
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
import static org.assertj.core.api.Assumptions.assumeThat;

/**
 * Integration tests that verify the actual llm_service contract.
 * These tests require llm_service running at http://localhost:8900.
 *
 * Purpose: diagnose why serving pipeline falls back from LLM to rule-based
 * paths by testing each llm_service endpoint with the exact payloads
 * that serving sends.
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

    // =========================================================================
    // Test 1: Execute endpoint — raw request/response structure
    // =========================================================================

    @Test
    @EnabledIf("isLlmServiceAvailable")
    @DisplayName("POST /api/v1/execute returns expected response structure")
    void execute_rawResponseStructure() {
        Map<String, Object> payload = Map.of(
                "pipeline_stage", "query_understanding",
                "template_key", "serving-query-understanding",
                "input", Map.of("query", "SMF是什么网元"),
                "caller_domain", "serving"
        );

        ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                BASE_URL + "/api/v1/execute",
                HttpMethod.POST,
                new HttpEntity<>(payload, jsonHeaders()),
                new ParameterizedTypeReference<>() {});

        assertThat(response.getStatusCode().is2xxSuccessful()).isTrue();
        Map<String, Object> body = response.getBody();
        assertThat(body).isNotNull();

        // DIAGNOSTIC: print the full response structure
        System.out.println("=== /execute response structure ===");
        printMap(body, 0);

        // Verify the response wrapper
        assertThat(body).containsKey("success");
        assertThat(body.get("success")).isEqualTo(true);

        // Verify data.result.parsed_output chain
        assertThat(body).containsKey("data");
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) body.get("data");
        assertThat(data).containsKey("result");

        @SuppressWarnings("unchecked")
        Map<String, Object> result = (Map<String, Object>) data.get("result");
        assertThat(result).containsKey("parsed_output");

        @SuppressWarnings("unchecked")
        Map<String, Object> parsed = (Map<String, Object>) result.get("parsed_output");
        assertThat(parsed).containsKey("intent");
        assertThat(parsed).containsKey("entities");
        assertThat(parsed).containsKey("keywords");

        System.out.println("\n=== parsed_output ===");
        parsed.forEach((k, v) -> System.out.println("  " + k + " = " + v));
    }

    // =========================================================================
    // Test 2: Simulate serving's LlmClient.execute() parsing logic
    // =========================================================================

    @Test
    @EnabledIf("isLlmServiceAvailable")
    @DisplayName("LlmClient.execute() response parsing — verify unwrapResponse fix")
    void execute_simulateServingParsing() {
        Map<String, Object> payload = Map.of(
                "pipeline_stage", "query_understanding",
                "template_key", "serving-query-understanding",
                "input", Map.of("query", "SMF是什么网元"),
                "caller_domain", "serving"
        );

        ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                BASE_URL + "/api/v1/execute",
                HttpMethod.POST,
                new HttpEntity<>(payload, jsonHeaders()),
                new ParameterizedTypeReference<>() {});

        Map<String, Object> result = response.getBody();

        // Verify the OLD parsing was broken (historical evidence)
        Object oldInnerObj = result.getOrDefault("result", result);
        @SuppressWarnings("unchecked")
        Map<String, Object> oldInner = (oldInnerObj instanceof Map) ? (Map<String, Object>) oldInnerObj : Map.of();
        @SuppressWarnings("unchecked")
        Map<String, Object> oldParsed = (Map<String, Object>) oldInner.getOrDefault("parsed_output", Map.of());
        System.out.println("=== OLD parsing (broken) ===");
        System.out.println("parsed is empty: " + oldParsed.isEmpty());

        // Verify the NEW parsing with unwrapResponse works
        Map<String, Object> data = LlmClient.unwrapResponse(result);
        Object resultObj = data.getOrDefault("result", data);
        @SuppressWarnings("unchecked")
        Map<String, Object> inner = (resultObj instanceof Map) ? (Map<String, Object>) resultObj : Map.of();
        @SuppressWarnings("unchecked")
        Map<String, Object> parsed = (Map<String, Object>) inner.getOrDefault("parsed_output", Map.of());

        System.out.println("=== NEW parsing (fixed) ===");
        System.out.println("parsed is empty: " + parsed.isEmpty());
        System.out.println("parsed keys: " + parsed.keySet());

        assertThat(parsed).isNotEmpty();
        assertThat(parsed).containsKey("intent");
    }

    // =========================================================================
    // Test 3: Correct parsing — unwrap "data" first
    // =========================================================================

    @Test
    @EnabledIf("isLlmServiceAvailable")
    @DisplayName("Correct parsing: unwrap data.result.parsed_output")
    void execute_correctParsing() {
        Map<String, Object> payload = Map.of(
                "pipeline_stage", "query_understanding",
                "template_key", "serving-query-understanding",
                "input", Map.of("query", "SMF是什么网元"),
                "caller_domain", "serving"
        );

        ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                BASE_URL + "/api/v1/execute",
                HttpMethod.POST,
                new HttpEntity<>(payload, jsonHeaders()),
                new ParameterizedTypeReference<>() {});

        Map<String, Object> body = response.getBody();

        // CORRECT parsing: body → data → result → parsed_output
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) body.get("data");
        @SuppressWarnings("unchecked")
        Map<String, Object> resultInner = (Map<String, Object>) data.get("result");
        @SuppressWarnings("unchecked")
        Map<String, Object> parsed = (Map<String, Object>) resultInner.get("parsed_output");

        System.out.println("=== Correct parsing ===");
        System.out.println("parsed keys: " + parsed.keySet());
        System.out.println("intent: " + parsed.get("intent"));

        assertThat(parsed).isNotEmpty();
        assertThat(parsed).containsKey("intent");
        assertThat(parsed.get("intent")).isNotNull();
    }

    // =========================================================================
    // Test 4: Embeddings endpoint
    // =========================================================================

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

    // =========================================================================
    // Test 5: Rerank endpoint
    // =========================================================================

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

    @SuppressWarnings("unchecked")
    private static void printMap(Map<String, Object> map, int depth) {
        String indent = "  ".repeat(depth);
        for (var e : map.entrySet()) {
            if (e.getValue() instanceof Map) {
                System.out.println(indent + e.getKey() + ":");
                printMap((Map<String, Object>) e.getValue(), depth + 1);
            } else if (e.getValue() instanceof List) {
                System.out.println(indent + e.getKey() + ": [" + ((List<?>) e.getValue()).size() + " items]");
            } else {
                System.out.println(indent + e.getKey() + " = " + e.getValue());
            }
        }
    }
}
