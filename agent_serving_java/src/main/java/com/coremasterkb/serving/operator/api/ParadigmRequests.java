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
        return toRunArgs(body, username, null, null, "api");
    }

    /** Compatibility overload for callers that have not yet propagated a trusted user id. */
    static RunArgs toRunArgs(
            JsonNode body, String username, String accessId, String source) {
        return toRunArgs(body, username, null, accessId, source);
    }

    static RunArgs toRunArgs(
            JsonNode body, String username, String userId, String accessId, String source) {
        String caller = (username != null && !username.isBlank()) ? username.trim() : null;
        String trustedUserId = (userId != null && !userId.isBlank()) ? userId.trim() : null;
        return new RunArgs(
                text(body, "query"), text(body, "domain"), text(body, "channel"),
                body != null && body.hasNonNull("debug") && body.get("debug").asBoolean(),
                caller,
                stringList(body, "kbIds"),
                mergedFilters(body),
                validatedTopK(body),
                validatedExpansion(body),
                null, null,
                accessId, source == null || source.isBlank() ? "api" : source,
                trustedUserId, safeRequestJson(body));
    }

    private static String safeRequestJson(JsonNode body) {
        if (body == null || !body.isObject()) return "{}";
        var safe = mapper().createObjectNode();
        for (String field : REQUEST_FIELDS) {
            JsonNode value = body.get(field);
            if (value == null || value.isNull()) continue;
            JsonNode sanitized = sanitize(value);
            if ("paradigm".equals(field) && sanitized.isObject()) {
                var graph = mapper().createObjectNode();
                for (String graphField : PARADIGM_FIELDS) {
                    JsonNode graphValue = sanitized.get(graphField);
                    if (graphValue != null && !graphValue.isNull()) graph.set(graphField, graphValue);
                }
                sanitized = graph;
            }
            safe.set(field, sanitized);
        }
        return safe.toString();
    }

    private static JsonNode sanitize(JsonNode value) {
        if (value == null || value.isNull() || value.isValueNode()) return value;
        if (value.isArray()) {
            var out = mapper().createArrayNode();
            value.forEach(item -> out.add(sanitize(item)));
            return out;
        }
        var out = mapper().createObjectNode();
        value.fields().forEachRemaining(entry -> {
            if (!sensitiveKey(entry.getKey())) out.set(entry.getKey(), sanitize(entry.getValue()));
        });
        return out;
    }

    private static boolean sensitiveKey(String key) {
        String normalized = key.toLowerCase().replaceAll("[^a-z0-9]", "");
        return SENSITIVE_MARKERS.stream().anyMatch(normalized::contains);
    }

    private static final Set<String> REQUEST_FIELDS = Set.of(
            "paradigm", "query", "domain", "channel", "debug", "kbIds",
            "within", "filters", "top_k", "expansion");
    private static final Set<String> PARADIGM_FIELDS = Set.of(
            "schemaVersion", "nodes", "edges", "output");
    private static final Set<String> SENSITIVE_MARKERS = Set.of(
            "authorization", "password", "passwd", "secret", "token", "cookie",
            "uploadurl", "ticket", "apikey", "accesskey", "mcpkey", "jwt");
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
        // A0-4：evidence_types 在此边界按公开词表校验并规范化为内部 representation_type
        // （list→list_group、code→code_block）——下游（pushdown/SQL）只见内部词。
        Map<String, Object> normalized = new LinkedHashMap<>();
        for (Map.Entry<String, Object> e : merged.entrySet()) {
            normalized.put(e.getKey(), normalizeFilterValue(e.getKey(), e.getValue()));
        }
        return Map.copyOf(normalized);
    }

    /** 单个 filter 值的形状校验 + 边界规范化（数组、非空串、长度上限、ref kind、类型枚举）。 */
    private static Object normalizeFilterValue(String key, Object value) {
        validateFilterValue(key, value);
        if ("directory_prefix".equals(key)) {
            return normalizeDirectoryPrefix(value);
        }
        if (!"evidence_types".equals(key)) {
            return value;
        }
        // A0-4：公开词 → 内部 representation_type（list→list_group、code→code_block；
        // 历史内部词恒等透传）——filter 面收公开词，存储面只有内部词。
        List<String> out = new ArrayList<>();
        for (Object item : (List<?>) value) {
            out.add(EvidenceTypeVocabulary.toRepresentationType((String) item));
        }
        return List.copyOf(out);
    }

    /** 单个 filter 值的形状校验（数组、非空串、长度上限、ref kind 匹配、类型枚举）。 */
    private static void validateFilterValue(String key, Object value) {
        // A2：section_scope 是字符串枚举（exact|descendants），不是数组。
        if ("section_scope".equals(key)) {
            if (!(value instanceof String mode)
                    || !com.coremasterkb.serving.domain.ActiveScope
                            .SECTION_SCOPE_MODES.contains(mode)) {
                throw new IllegalArgumentException(
                        "filter_value_invalid:section_scope: 必须是 exact 或 descendants");
            }
            return;
        }
        // 57号：directory_prefix 是单个字符串（目录路径，含子目录递归）。
        // 这里只做廉价形状检查（类型 + 原始长度上限）；完整规范化在
        // normalizeFilterValue 只执行一次（避免每请求重复整串处理）。
        if ("directory_prefix".equals(key)) {
            if (!(value instanceof String raw) || raw.isEmpty()) {
                throw new IllegalArgumentException(
                        "filter_value_invalid:directory_prefix: 必须是非空字符串（目录路径，如 产品文档/手册）");
            }
            if (raw.length() > MAX_DIRECTORY_PREFIX_LENGTH + 64) {
                throw new IllegalArgumentException(
                        "filter_value_invalid:directory_prefix: 长度超过上限 "
                                + MAX_DIRECTORY_PREFIX_LENGTH);
            }
            return;
        }
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
                    if (EvidenceTypeVocabulary.toRepresentationType(s) == null) {
                        throw new IllegalArgumentException(
                                "filter_value_invalid:evidence_types: 未知类型 " + s
                                        + "；允许：" + EvidenceTypeVocabulary.PUBLIC_TYPES);
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

    /**
     * 57号：directory_prefix 规范化（trim + 去首尾斜杠）——非法形状 typed 400，
     * 绝不静默退化成宽检索（与其他 filter 键同一边界纪律）。
     *
     * <p>codex P1：索引线性扫描、单次截取——绝不用循环 substring 逐字符剥
     * （超长斜杠串会 O(n²) 复制，请求线程 DoS）；原始长度上限在
     * validateFilterValue 已先拒，这里只做防御性复查。</p>
     */
    static String normalizeDirectoryPrefix(Object value) {
        if (!(value instanceof String raw)) {
            throw new IllegalArgumentException(
                    "filter_value_invalid:directory_prefix: 必须是非空字符串（目录路径，如 产品文档/手册）");
        }
        if (raw.length() > MAX_DIRECTORY_PREFIX_LENGTH + 64) {
            throw new IllegalArgumentException(
                    "filter_value_invalid:directory_prefix: 长度超过上限 "
                            + MAX_DIRECTORY_PREFIX_LENGTH);
        }
        // trim（ASCII 空白）+ 去首尾 '/'：全索引扫描，一次 substring
        int start = 0;
        int end = raw.length();
        while (start < end && (raw.charAt(start) == ' ' || raw.charAt(start) == '/')) start++;
        while (end > start && (raw.charAt(end - 1) == ' ' || raw.charAt(end - 1) == '/')) end--;
        String normalized = raw.substring(start, end);
        if (normalized.isEmpty() || normalized.length() > MAX_DIRECTORY_PREFIX_LENGTH) {
            throw new IllegalArgumentException(
                    "filter_value_invalid:directory_prefix: 规范化后必须非空且长度 ≤ "
                            + MAX_DIRECTORY_PREFIX_LENGTH);
        }
        if (normalized.contains("\\") || normalized.startsWith("doc:")) {
            throw new IllegalArgumentException(
                    "filter_value_invalid:directory_prefix: 路径分隔符只认 /，且不接受 doc: 开头的 document ref");
        }
        for (String segment : normalized.split("/", -1)) {
            if (segment.isBlank() || ".".equals(segment) || "..".equals(segment)) {
                throw new IllegalArgumentException(
                        "filter_value_invalid:directory_prefix: 含空段或点段（./..）");
            }
        }
        return normalized;
    }

    /** 源内容类型枚举（asset_types 值域；projector content_type 词表）。 */
    private static final Set<String> ASSET_TYPES = Set.of(
            "paragraph", "table", "table_row", "list", "code", "formula",
            "figure", "figure_caption", "section", "document");

    private static final int MAX_FILTER_VALUES = 64;

    /** 57号：directory_prefix 规范化后的长度上限。 */
    private static final int MAX_DIRECTORY_PREFIX_LENGTH = 512;

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
