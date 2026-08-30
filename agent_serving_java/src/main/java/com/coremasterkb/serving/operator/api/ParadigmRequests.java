package com.coremasterkb.serving.operator.api;

import com.coremasterkb.serving.operator.api.ParadigmExecutionService.RunArgs;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Small helpers for reading paradigm request bodies. */
final class ParadigmRequests {

    private ParadigmRequests() {}

    /** §7.1 expansion.mode 白名单（与 evidence_hydrate 的 mode 枚举一致）。 */
    private static final Set<String> EXPANSION_MODES = Set.of(
            "auto", "exact", "window", "parent", "whole_document");

    static RunArgs toRunArgs(JsonNode body) {
        return toRunArgs(body, null);
    }

    /** @param username the {@code X-KB-User} header value, or null when the caller sent none */
    static RunArgs toRunArgs(JsonNode body, String username) {
        String caller = (username != null && !username.isBlank()) ? username.trim() : null;
        return new RunArgs(
                text(body, "query"), text(body, "domain"), text(body, "channel"),
                body != null && body.hasNonNull("debug") && body.get("debug").asBoolean(),
                caller,
                stringList(body, "kbIds"),
                mergedFilters(body),
                validatedTopK(body),
                validatedExpansion(body),
                null, null);
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

    /**
     * R8（§7.1）：显式 {@code within} 与 {@code filters} 平铺合并为 requestFilters——
     * 显式传入 = hard filter（scope_resolve 透传、召回 Top-K 前下推）；未传 = 宽检索。
     * 服务端不从 query 推断任何 filter。
     *
     * <p>27号审查修复：键白名单在请求边界校验（400），未支持的键不再静默忽略——
     * 否则调用方以为过滤生效，实际返回的是全范围数据。</p>
     */
    static Map<String, Object> mergedFilters(JsonNode body) {
        if (body == null) return Map.of();
        Map<String, Object> merged = new LinkedHashMap<>();
        copyObject(merged, body.get("within"));
        copyObject(merged, body.get("filters"));
        if (merged.isEmpty()) return Map.of();
        for (String key : merged.keySet()) {
            if (!com.coremasterkb.serving.domain.ActiveScope.SUPPORTED_FILTER_KEYS.contains(key)) {
                throw new IllegalArgumentException("unsupported_scope_filter:" + key);
            }
        }
        return Map.copyOf(merged);
    }

    private static void copyObject(Map<String, Object> target, JsonNode node) {
        if (node == null || !node.isObject()) return;
        node.fields().forEachRemaining(e -> {
            if (e.getValue() != null && !e.getValue().isNull()) {
                target.put(e.getKey(), mapper().<Object>convertValue(e.getValue(), Object.class));
            }
        });
    }

    private static com.fasterxml.jackson.databind.ObjectMapper mapper() {
        return Json.MAPPER;
    }

    /** §7.1 top_k：正整数；缺失/非法 → IllegalArgumentException（400）。 */
    static Integer validatedTopK(JsonNode body) {
        if (body == null) return null;
        JsonNode v = body.get("top_k");
        if (v == null || v.isNull()) return null;
        if (!v.isInt() || v.asInt() <= 0) {
            throw new IllegalArgumentException("top_k_invalid");
        }
        return v.asInt();
    }

    /** §7.1 expansion.mode：白名单；缺失 = null（不覆盖）；非法 → 400。 */
    static String validatedExpansion(JsonNode body) {
        if (body == null) return null;
        JsonNode v = body.get("expansion");
        if (v == null || !v.isObject()) return null;
        JsonNode mode = v.get("mode");
        if (mode == null || mode.isNull()) return null;
        if (!mode.isTextual() || !EXPANSION_MODES.contains(mode.asText())) {
            throw new IllegalArgumentException("expansion_invalid");
        }
        return mode.asText();
    }

    /** Extract the {@code graph} object from a body as a compact JSON string, or null if absent. */
    static String graphString(JsonNode body) {
        if (body == null) return null;
        JsonNode g = body.get("graph");
        return (g != null && !g.isNull()) ? g.toString() : null;
    }

    /** Lazy holder：ObjectMapper 无状态共享。 */
    private static final class Json {
        private static final com.fasterxml.jackson.databind.ObjectMapper MAPPER =
                new com.fasterxml.jackson.databind.ObjectMapper();
    }
}
