package com.coremasterkb.serving.domain;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * SearchRequest gained a {@code kbIds} component plus a shorter secondary constructor. Records
 * with more than one constructor are exactly where Jackson can silently bind the wrong one, and
 * the failure mode — kbIds always empty, so KB narrowing quietly does nothing — would not show up
 * in any other test.
 */
@DisplayName("SearchRequest JSON binding")
class SearchRequestJsonTest {

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    @DisplayName("kbIds binds from camelCase")
    void bindsCamelCase() throws Exception {
        var req = mapper.readValue(
                "{\"query\":\"SMF\",\"kbIds\":[\"kb1\",\"kb2\"]}", SearchRequest.class);
        assertThat(req.kbIds()).containsExactly("kb1", "kb2");
    }

    @Test
    @DisplayName("kbIds also binds from snake_case, matching the response style")
    void bindsSnakeCase() throws Exception {
        var req = mapper.readValue(
                "{\"query\":\"SMF\",\"kb_ids\":[\"kb1\"]}", SearchRequest.class);
        assertThat(req.kbIds()).containsExactly("kb1");
    }

    @Test
    @DisplayName("absent kbIds defaults to empty, keeping pre-existing callers domain-wide")
    void defaultsToEmpty() throws Exception {
        var req = mapper.readValue(
                "{\"query\":\"SMF\",\"domain\":\"cloud_core_network\"}", SearchRequest.class);
        assertThat(req.kbIds()).isEmpty();
        assertThat(req.mode()).isEqualTo("evidence");
    }

    @Test
    @DisplayName("other fields still bind after the record grew a component")
    void otherFieldsStillBind() throws Exception {
        var req = mapper.readValue(
                "{\"query\":\"SMF\",\"domain\":\"d1\",\"channel\":\"prod\",\"debug\":true,"
                        + "\"mode\":\"raw\",\"scope\":{\"product\":\"UNC\"}}",
                SearchRequest.class);
        assertThat(req.domain()).isEqualTo("d1");
        assertThat(req.channel()).isEqualTo("prod");
        assertThat(req.debug()).isTrue();
        assertThat(req.mode()).isEqualTo("raw");
        assertThat(req.scope()).containsEntry("product", "UNC");
    }
}
