package com.coremasterkb.serving.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("JsonUtils")
class JsonUtilsTest {

    @Nested
    @DisplayName("safeJsonParse")
    class SafeJsonParse {
        @Test
        @DisplayName("null returns empty map")
        void nullReturnsEmpty() {
            assertThat(JsonUtils.safeJsonParse(null)).isEmpty();
        }

        @Test
        @DisplayName("Map input returned as-is")
        void mapInputReturnedDirectly() {
            Map<String, Object> map = Map.of("key", "value");
            assertThat(JsonUtils.safeJsonParse(map)).isSameAs(map);
        }

        @Test
        @DisplayName("valid JSON string parsed correctly")
        void validJsonStringParsed() {
            String json = "{\"name\": \"test\", \"count\": 42}";
            var result = JsonUtils.safeJsonParse(json);
            assertThat(result).containsEntry("name", "test");
            assertThat(result).containsEntry("count", 42);
        }

        @Test
        @DisplayName("blank string returns empty map")
        void blankStringReturnsEmpty() {
            assertThat(JsonUtils.safeJsonParse("  ")).isEmpty();
        }

        @Test
        @DisplayName("invalid JSON string returns empty map")
        void invalidStringReturnsEmpty() {
            assertThat(JsonUtils.safeJsonParse("not json")).isEmpty();
        }

        @Test
        @DisplayName("non-string non-map returns empty map")
        void nonStringNonMapReturnsEmpty() {
            assertThat(JsonUtils.safeJsonParse(123)).isEmpty();
        }
    }

    @Nested
    @DisplayName("parseJson")
    class ParseJson {
        @Test
        @DisplayName("delegates to safeJsonParse")
        void delegatesToSafeJsonParse() {
            String json = "{\"a\": 1}";
            assertThat(JsonUtils.parseJson(json)).isEqualTo(JsonUtils.safeJsonParse(json));
        }
    }
}
