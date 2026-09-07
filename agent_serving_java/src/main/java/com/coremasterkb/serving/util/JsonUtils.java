package com.coremasterkb.serving.util;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.Collections;
import java.util.Map;

/**
 * JSON 解析工具（A1 37 号 D2：随 fulltext 链退役删除了解析旧格式
 * {@code raw_segment_ids} 的 parseSourceRefs/parseTargetRef——挖掘侧现写
 * {@code {element_id, evidence_span_ids}}，旧解析器零调用方且格式漂移）。
 */
public final class JsonUtils {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private JsonUtils() {}

    /**
     * safeJsonParse -- parse raw string to Map; return empty map on failure or if already a Map.
     */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> safeJsonParse(Object raw) {
        if (raw == null) {
            return Collections.emptyMap();
        }
        if (raw instanceof Map<?, ?> m) {
            return (Map<String, Object>) m;
        }
        if (raw instanceof String s) {
            if (s.isBlank()) return Collections.emptyMap();
            try {
                return MAPPER.readValue(s, new TypeReference<>() {});
            } catch (Exception e) {
                return Collections.emptyMap();
            }
        }
        return Collections.emptyMap();
    }

    /**
     * Parse a JSON string into a generic Map (null-safe).
     */
    public static Map<String, Object> parseJson(String json) {
        return safeJsonParse(json);
    }

    /** Shared ObjectMapper instance for use by other components. */
    public static ObjectMapper mapper() {
        return MAPPER;
    }
}
