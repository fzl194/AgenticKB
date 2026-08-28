package com.coremasterkb.serving.operator.api;

import com.coremasterkb.serving.operator.api.ParadigmExecutionService.RunArgs;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.List;

/** Small helpers for reading paradigm request bodies. */
final class ParadigmRequests {

    private ParadigmRequests() {}

    static RunArgs toRunArgs(JsonNode body) {
        return toRunArgs(body, null);
    }

    /** @param username the {@code X-KB-User} header value, or null when the caller sent none */
    static RunArgs toRunArgs(JsonNode body, String username) {
        String caller = (username != null && !username.isBlank()) ? username.trim() : null;
        return new RunArgs(text(body, "query"), text(body, "domain"), text(body, "channel"),
                body != null && body.hasNonNull("debug") && body.get("debug").asBoolean(),
                caller)
                .withKbIds(stringList(body, "kbIds"));
    }

    static String text(JsonNode body, String field) {
        if (body == null) return null;
        JsonNode v = body.get(field);
        return (v != null && v.isTextual() && !v.asText().isBlank()) ? v.asText() : null;
    }

    /** 阶段 A：请求级库范围 {@code kbIds}（字符串数组；缺失/非数组/空 → 空列表）。 */
    static List<String> stringList(JsonNode body, String field) {
        if (body == null) return List.of();
        JsonNode v = body.get(field);
        if (v == null || !v.isArray()) return List.of();
        List<String> out = new ArrayList<>();
        for (JsonNode item : v) {
            if (item != null && item.isTextual() && !item.asText().isBlank()) {
                out.add(item.asText());
            }
        }
        return List.copyOf(out);
    }

    /** Extract the {@code graph} object from a body as a compact JSON string, or null if absent. */
    static String graphString(JsonNode body) {
        if (body == null) return null;
        JsonNode g = body.get("graph");
        return (g != null && !g.isNull()) ? g.toString() : null;
    }
}
