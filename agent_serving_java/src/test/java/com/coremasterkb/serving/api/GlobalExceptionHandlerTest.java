package com.coremasterkb.serving.api;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("GlobalExceptionHandler")
class GlobalExceptionHandlerTest {

    private final GlobalExceptionHandler handler = new GlobalExceptionHandler();

    @Test
    @DisplayName("query_required -> 400")
    void queryRequired() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalArgument(new IllegalArgumentException("query_required"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(resp.getBody()).containsEntry("error", "query_required");
    }

    @Test
    @DisplayName("unknown_domain -> 400")
    void unknownDomain() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalArgument(new IllegalArgumentException("unknown_domain"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(resp.getBody()).containsEntry("error", "unknown_domain");
    }

    @Test
    @DisplayName("domain_disabled -> 400")
    void domainDisabled() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalArgument(new IllegalArgumentException("domain_disabled"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(resp.getBody()).containsEntry("error", "domain_disabled");
    }

    @Test
    @DisplayName("no_active_release -> 503")
    void noActiveRelease() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalArgument(new IllegalArgumentException("no_active_release"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        assertThat(resp.getBody()).containsEntry("error", "no_active_release");
    }

    @Test
    @DisplayName("multiple_active_releases -> 409")
    void multipleActiveReleases() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalArgument(new IllegalArgumentException("multiple_active_releases"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.CONFLICT);
        assertThat(resp.getBody()).containsEntry("error", "multiple_active_releases");
    }

    @Test
    @DisplayName("scenario_pack_missing -> 500")
    void scenarioPackMissing() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalState(new IllegalStateException("scenario_pack_missing"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);
        assertThat(resp.getBody()).containsEntry("error", "scenario_pack_missing");
    }

    @Test
    @DisplayName("domain_database_unavailable -> 503")
    void domainDatabaseUnavailable() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalState(new IllegalStateException("domain_database_unavailable"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        assertThat(resp.getBody()).containsEntry("error", "domain_database_unavailable");
    }

    @Test
    @DisplayName("unknown IllegalArgumentException -> 400 bad_request")
    void unknownIllegalArgument() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalArgument(new IllegalArgumentException("some_other_error"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(resp.getBody()).containsEntry("error", "bad_request");
    }

    @Test
    @DisplayName("kb_not_found -> 404 (same answer for forbidden and nonexistent)")
    void kbNotFound() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalArgument(new IllegalArgumentException("kb_not_found"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(resp.getBody()).containsEntry("error", "kb_not_found");
    }

    @Test
    @DisplayName("no_active_kb_build -> 404")
    void noActiveKbBuild() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalArgument(new IllegalArgumentException("no_active_kb_build"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(resp.getBody()).containsEntry("error", "no_active_kb_build");
    }

    @Test
    @DisplayName("kb_ids_required -> 400")
    void kbIdsRequired() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalArgument(new IllegalArgumentException("kb_ids_required"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(resp.getBody()).containsEntry("error", "kb_ids_required");
    }

    @Test
    @DisplayName("unknown IllegalStateException -> 500 internal_error")
    void unknownIllegalState() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleIllegalState(new IllegalStateException("some_unexpected_state"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);
        assertThat(resp.getBody()).containsEntry("error", "internal_error");
    }

    @Test
    @DisplayName("generic exception -> 500 internal_error")
    void genericException() {
        ResponseEntity<Map<String, Object>> resp =
                handler.handleGeneric(new RuntimeException("boom"));
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);
        assertThat(resp.getBody()).containsEntry("error", "internal_error");
    }
}
