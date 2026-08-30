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
        // 29号 R06a：值 schema 校验——错误类型必须 typed 400，绝不静默退化成
        // 宽检索（此前 stringValues 遇非数组直接返回空 = 全量结果）。
        for (Map.Entry<String, Object> e : merged.entrySet()) {
            validateFilterValue(e.getKey(), e.getValue());
        }
        return Map.copyOf(merged);
    }

    /** 单个 filter 值的形状校验（数组、非空串、长度上限、ref kind 匹配、类型枚举）。 */
    private static void validateFilterValue(String key, Object value) {
        if (!(value instanceof List<?> list)) {
            throw new IllegalArgumentException(
                    "filter_value_invalid:" + key + ": 必须是字符串数组");
        }
        if (list.size() > MAX_FILTER_VALUES) {
            throw new IllegalArgumentException(
                    "filter_value_invalid:" + key + ": 超过 " + MAX_FILTER_VALUES + " 项");
        }
        for (Object item : list) {
            if (!(item instanceof String s) || s.isBlank()) {
                throw new IllegalArgumentException(
                        "filter_value_invalid:" + key + ": 数组元素必须是非空字符串");
            }
            switch (key) {
                case "document_refs" -> {
                    if (s.startsWith("st_") || s.startsWith("ev_")) {
                        throw new IllegalArgumentException(
                                "filter_value_invalid:document_refs: 不接受 " + prefixOf(s) + " ref（用 doc_ 或明文内部 ref）");
                    }
                }
                case "section_refs" -> {
                    if (s.startsWith("doc_") || s.startsWith("ev_")) {
                        throw new IllegalArgumentException(
                                "filter_value_invalid:section_refs: 不接受 " + prefixOf(s) + " ref（用 st_ 或明文内部 ref）");
                    }
                }
                case "evidence_types" -> {
                    if (!EVIDENCE_TYPES.contains(s)) {
                        throw new IllegalArgumentException(
                                "filter_value_invalid:evidence_types: 未知类型 " + s + "；允许：" + EVIDENCE_TYPES);
                    }
                }
                case "asset_types" -> {
                    if (!ASSET_TYPES.contains(s)) {
                        throw new IllegalArgumentException(
                                "filter_value_invalid:asset_types: 未知类型 " + s + "；允许：" + ASSET_TYPES);
                    }
                }
                default -> { }
            }
        }
    }

    private static String prefixOf(String ref) {
        int cut = Math.min(ref.length(), 4);
        return ref.substring(0, ref.indexOf('_') > 0 ? Math.min(ref.indexOf('_') + 1, cut + 1) : cut) + "…";
    }

    /** §5.3 representation type 枚举（evidence_types 值域）。 */
    private static final Set<String> EVIDENCE_TYPES = Set.of(
            "prose", "section", "document", "table", "table_row", "list_group",
            "code_block", "formula", "figure_caption", "query_alias", "summary_alias");

    /** 源内容类型枚举（asset_types 值域；projector content_type 词表）。 */
    private static final Set<String> ASSET_TYPES = Set.of(
            "paragraph", "table", "table_row", "list", "code", "formula",
            "figure", "figure_caption", "section", "document");

    private static final int MAX_FILTER_VALUES = 64;

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
