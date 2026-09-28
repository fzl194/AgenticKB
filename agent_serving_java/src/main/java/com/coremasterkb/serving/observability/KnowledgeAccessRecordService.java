package com.coremasterkb.serving.observability;

import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.entity.KnowledgeAccessRecord;
import com.coremasterkb.serving.entity.KnowledgeAccessRecordPayload;
import com.coremasterkb.serving.mapper.KnowledgeAccessRecordMapper;
import com.coremasterkb.serving.operator.api.ParadigmExecutionService.RunArgs;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicLong;

@Component
public class KnowledgeAccessRecordService {
    private static final Logger log = LoggerFactory.getLogger(KnowledgeAccessRecordService.class);
    private static final Set<String> SOURCES = Set.of("web", "mcp", "api");
    private static final Set<String> REQUEST_FIELDS = Set.of(
            "paradigm", "query", "domain", "channel", "debug", "kbIds",
            "kb_ids", "within", "filters", "top_k", "expansion");
    private static final Set<String> RESPONSE_FIELDS = Set.of(
            "evidenceResponse", "query", "evidence", "has_more", "hasMore",
            "diagnostics", "trace", "candidates", "rows", "items", "results",
            "segments", "documents", "nodes", "cursor", "error", "message");
    private static final Set<String> COLLECTION_FIELDS = Set.of(
            "evidence", "rows", "items", "results", "segments", "documents", "nodes", "candidates");
    private static final Set<String> SENSITIVE_MARKERS = Set.of(
            "authorization", "password", "passwd", "secret", "token", "cookie",
            "uploadurl", "ticket", "apikey", "accesskey", "mcpkey", "jwt");
    private static final int QUERY_PREVIEW_CODE_POINTS = 4_000;
    private static final int REQUEST_MAX_BYTES = 256 * 1024;
    private static final int RESPONSE_MAX_BYTES = 4 * 1024 * 1024;

    private final KnowledgeAccessRecordMapper mapper;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final AtomicLong writeFailures = new AtomicLong();

    public KnowledgeAccessRecordService(KnowledgeAccessRecordMapper mapper) {
        this.mapper = mapper;
    }

    public void record(
            Instant startedAt, RunArgs args, Object result, Throwable failure, long durationMs) {
        KnowledgeAccessRecord record = build(startedAt, args, result, failure, durationMs);
        final int transitioned;
        try {
            transitioned = mapper.insert(record);
        } catch (Exception e) {
            onWriteFailure(record.getId(), "search", "record", e);
            return;
        }
        // A terminal replay must not overwrite the payload that won the state transition.
        // MCP's trusted finalizer uses the Mining internal endpoint and owns the final user snapshot.
        if (transitioned <= 0) return;
        try {
            mapper.upsertPayload(buildPayload(record, args, result, failure));
        } catch (Exception e) {
            onWriteFailure(record.getId(), "search", "payload", e);
        }
    }

    public long writeFailures() {
        return writeFailures.get();
    }

    private void onWriteFailure(String recordId, String tool, String phase, Exception failure) {
        writeFailures.incrementAndGet();
        log.warn("access_record_write_failed record_id={} tool={} phase={} error_class={}",
                recordId, tool, phase, failure.getClass().getSimpleName());
    }

    private KnowledgeAccessRecord build(
            Instant startedAt, RunArgs args, Object result, Throwable failure, long durationMs) {
        EvidenceResponse evidence = extractEvidence(result);
        Integer resultCount = evidence != null
                ? Integer.valueOf(evidence.evidence().size()) : candidateCount(result);

        KnowledgeAccessRecord record = new KnowledgeAccessRecord();
        record.setId(nonBlank(args.accessId(), UUID.randomUUID().toString()));
        record.setOccurredAt(startedAt.toString());
        record.setCompletedAt(Instant.now().toString());
        record.setDomain(nonBlank(args.domain(), "default"));
        record.setActorUserId(blankToNull(args.userId()));
        record.setActorUsername(blankToNull(args.username()));
        record.setSource(SOURCES.contains(args.source()) ? args.source() : "api");
        record.setOperation("search");
        record.setKbIdsJson(toJson(args.kbIds() == null ? List.of() : args.kbIds(), "[]"));
        record.setQueryText(preview(args.query()));
        record.setParadigmId(args.paradigmId());
        record.setParadigmVersion(args.paradigmVersion());
        record.setResultCount(failure == null ? resultCount : null);
        record.setDurationMs((int) Math.min(Math.max(durationMs, 0), Integer.MAX_VALUE));

        if (failure == null) {
            record.setStatus(resultCount != null && resultCount == 0 ? "no_result" : "success");
        } else if (isTimeout(failure)) {
            record.setStatus("timeout");
            record.setErrorCode("execution_timeout");
        } else if (failure instanceof SecurityException) {
            record.setStatus("denied");
            record.setErrorCode("access_denied");
        } else if (failure instanceof IllegalArgumentException) {
            record.setStatus("invalid");
            record.setErrorCode("invalid_request");
        } else {
            record.setStatus("failed");
            record.setErrorCode("execution_failed");
        }

        Map<String, Object> details = new LinkedHashMap<>();
        details.put("engine", "paradigm");
        details.put("output", outputKind(result));
        if (args.channel() != null && !args.channel().isBlank()) details.put("channel", args.channel());
        if (evidence != null) details.put("has_more", evidence.hasMore());
        boolean accessIdMissing = args.accessId() == null || args.accessId().isBlank();
        boolean identityFallback = !"mcp".equals(args.source())
                && args.username() != null && !args.username().isBlank()
                && (args.userId() == null || args.userId().isBlank());
        if (accessIdMissing || identityFallback) details.put("attribution_degraded", true);
        if ("mcp".equals(record.getSource())) details.put("terminal_owner", "serving");
        record.setDetailsJson(toJson(details, "{}"));
        return record;
    }

    private KnowledgeAccessRecordPayload buildPayload(
            KnowledgeAccessRecord record, RunArgs args, Object result, Throwable failure) {
        List<String> redactions = new ArrayList<>();
        JsonNode request = safeRequest(args, redactions);
        BoundedJson boundedRequest = bound(request, REQUEST_MAX_BYTES);
        JsonNode effective = sanitizeNode(objectMapper.valueToTree(effectiveView(args)),
                "effective_context", redactions, null);

        JsonNode response = failure == null && result != null
                ? sanitizeNode(objectMapper.valueToTree(result), "", redactions, RESPONSE_FIELDS)
                : publicError(record);
        BoundedJson boundedResponse = bound(response, RESPONSE_MAX_BYTES);
        JsonNode refs = sanitizeNode(objectMapper.valueToTree(responseRefs(result)),
                "response_refs", redactions, null);

        KnowledgeAccessRecordPayload payload = new KnowledgeAccessRecordPayload();
        payload.setRecordId(record.getId());
        payload.setRequestJson(boundedRequest.json());
        payload.setEffectiveContextJson(toJson(effective, "{}"));
        payload.setResponseMode(failure == null && result != null ? "snapshot" : "summary");
        payload.setResponseJson(boundedResponse.json());
        payload.setResponseRefsJson(toJson(refs, "[]"));
        payload.setRequestBytes(boundedRequest.storedBytes());
        payload.setResponseBytes(boundedResponse.storedBytes());
        payload.setResponseTruncated(boundedResponse.truncated());
        payload.setResponseOriginalBytes(boundedResponse.originalBytes());
        payload.setResponseOmittedCount(boundedResponse.omittedCount());
        payload.setResponseSha256(boundedResponse.sha256());
        payload.setRedactionsJson(toJson(redactions.stream().distinct().sorted().toList(), "[]"));
        payload.setPayloadSchemaVersion(1);
        return payload;
    }

    private JsonNode safeRequest(RunArgs args, List<String> redactions) {
        JsonNode raw;
        try {
            raw = args.requestJson() == null || args.requestJson().isBlank()
                    ? objectMapper.valueToTree(requestView(args))
                    : objectMapper.readTree(args.requestJson());
        } catch (Exception invalidJson) {
            raw = objectMapper.valueToTree(requestView(args));
        }
        return sanitizeNode(raw, "", redactions, REQUEST_FIELDS);
    }

    private JsonNode publicError(KnowledgeAccessRecord record) {
        String code = nonBlank(record.getErrorCode(), "execution_failed");
        String message = switch (code) {
            case "invalid_request" -> "请求参数无效。";
            case "access_denied" -> "无权访问请求的知识内容。";
            case "execution_timeout" -> "检索执行超时。";
            default -> "检索执行失败，请稍后重试。";
        };
        return objectMapper.valueToTree(Map.of("error", code, "message", message));
    }

    private JsonNode sanitizeNode(
            JsonNode value, String path, List<String> redactions, Set<String> rootWhitelist) {
        if (value == null || value.isNull() || value.isValueNode()) return value;
        if (value.isArray()) {
            ArrayNode out = objectMapper.createArrayNode();
            int index = 0;
            for (JsonNode item : value) {
                out.add(sanitizeNode(item, path + "[" + index + "]", redactions, null));
                index++;
            }
            return out;
        }
        ObjectNode out = objectMapper.createObjectNode();
        value.fields().forEachRemaining(entry -> {
            String key = entry.getKey();
            String childPath = path.isBlank() ? key : path + "." + key;
            if (rootWhitelist != null && !rootWhitelist.contains(key)) return;
            if (sensitiveKey(key)) {
                redactions.add(childPath);
                return;
            }
            out.set(key, sanitizeNode(entry.getValue(), childPath, redactions, null));
        });
        return out;
    }

    private BoundedJson bound(JsonNode value, int maxBytes) {
        String original = toJson(value, "{}");
        int originalBytes = byteLength(original);
        String digest = sha256(original);
        if (originalBytes <= maxBytes) {
            return new BoundedJson(original, originalBytes, originalBytes, false, 0, digest);
        }
        List<String> collectionPath = findCollectionPath(value, new ArrayList<>());
        if (collectionPath != null) {
            JsonNode copy = value.deepCopy();
            ArrayNode source = (ArrayNode) atPath(value, collectionPath);
            ObjectNode parent = (ObjectNode) atPath(copy, collectionPath.subList(0, collectionPath.size() - 1));
            String field = collectionPath.get(collectionPath.size() - 1);
            ArrayNode target = objectMapper.createArrayNode();
            parent.set(field, target);
            for (JsonNode item : source) {
                target.add(item.deepCopy());
                if (byteLength(toJson(copy, "{}")) > maxBytes) {
                    target.remove(target.size() - 1);
                    break;
                }
            }
            String bounded = toJson(copy, "{}");
            if (byteLength(bounded) <= maxBytes) {
                return new BoundedJson(bounded, byteLength(bounded), originalBytes, true,
                        Math.max(1, source.size() - target.size()), digest);
            }
        }
        ObjectNode envelope = objectMapper.createObjectNode();
        envelope.put("__truncated__", true);
        envelope.put("__original_bytes__", originalBytes);
        envelope.put("__sha256__", digest);
        String preview = original;
        while (!preview.isEmpty()) {
            envelope.put("__preview__", preview);
            String encoded = toJson(envelope, "{}");
            int bytes = byteLength(encoded);
            if (bytes <= maxBytes) {
                return new BoundedJson(encoded, bytes, originalBytes, true, 1, digest);
            }
            int reduce = Math.max(1, bytes - maxBytes);
            preview = preview.substring(0, Math.max(0, preview.length() - reduce));
        }
        envelope.put("__preview__", "");
        String encoded = toJson(envelope, "{}");
        return new BoundedJson(encoded, byteLength(encoded), originalBytes, true, 1, digest);
    }

    private List<String> findCollectionPath(JsonNode value, List<String> path) {
        if (!value.isObject()) return null;
        for (String key : COLLECTION_FIELDS) {
            JsonNode candidate = value.get(key);
            if (candidate != null && candidate.isArray() && !candidate.isEmpty()) {
                List<String> found = new ArrayList<>(path);
                found.add(key);
                return found;
            }
        }
        var fields = value.fields();
        while (fields.hasNext()) {
            var entry = fields.next();
            if (!entry.getValue().isObject()) continue;
            List<String> nestedPath = new ArrayList<>(path);
            nestedPath.add(entry.getKey());
            List<String> found = findCollectionPath(entry.getValue(), nestedPath);
            if (found != null) return found;
        }
        return null;
    }

    private static JsonNode atPath(JsonNode root, List<String> path) {
        JsonNode current = root;
        for (String item : path) current = current.get(item);
        return current;
    }

    private static boolean sensitiveKey(String key) {
        String normalized = key.toLowerCase().replaceAll("[^a-z0-9]", "");
        return SENSITIVE_MARKERS.stream().anyMatch(normalized::contains);
    }

    private static Map<String, Object> requestView(RunArgs args) {
        Map<String, Object> request = new LinkedHashMap<>();
        request.put("query", args.query());
        request.put("domain", args.domain());
        request.put("channel", args.channel());
        request.put("debug", args.debug());
        request.put("kb_ids", args.kbIds() == null ? List.of() : args.kbIds());
        request.put("filters", args.filters() == null ? Map.of() : args.filters());
        request.put("top_k", args.topK());
        request.put("expansion", args.expansion());
        return request;
    }

    private static Map<String, Object> effectiveView(RunArgs args) {
        Map<String, Object> effective = new LinkedHashMap<>(requestView(args));
        effective.put("paradigm_id", args.paradigmId());
        effective.put("paradigm_version", args.paradigmVersion());
        return effective;
    }

    private static List<Map<String, Object>> responseRefs(Object result) {
        EvidenceResponse evidence = extractEvidence(result);
        if (evidence == null) return List.of();
        List<Map<String, Object>> refs = new ArrayList<>();
        for (EvidenceResponse.EvidenceItem item : evidence.evidence()) {
            Map<String, Object> ref = new LinkedHashMap<>();
            ref.put("ref", item.ref());
            ref.put("structure_ref", item.structureRef());
            if (item.source() != null) {
                ref.put("document_ref", item.source().documentRef());
                ref.put("knowledge_base", item.source().knowledgeBase());
                ref.put("file_name", item.source().fileName());
            }
            refs.add(ref);
        }
        return refs;
    }

    private static EvidenceResponse extractEvidence(Object result) {
        if (result instanceof Map<?, ?> map
                && map.get("evidenceResponse") instanceof EvidenceResponse evidence) return evidence;
        return null;
    }

    private static Integer candidateCount(Object result) {
        if (result instanceof Map<?, ?> map && map.get("candidates") instanceof List<?> values) {
            return values.size();
        }
        if (result instanceof Map<?, ?> map && map.get("evidence") instanceof List<?> values) {
            return values.size();
        }
        return null;
    }

    private static String outputKind(Object result) {
        if (!(result instanceof Map<?, ?> map)) return "none";
        if (map.containsKey("evidenceResponse") || map.containsKey("evidence")) return "evidence";
        if (map.containsKey("candidates")) return "candidates";
        return "other";
    }

    private static boolean isTimeout(Throwable failure) {
        Throwable current = failure;
        while (current != null) {
            if (current instanceof TimeoutException || current instanceof SocketTimeoutException) return true;
            current = current.getCause();
        }
        return false;
    }

    private String toJson(Object value, String fallback) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception e) {
            return fallback;
        }
    }

    private static String preview(String value) {
        if (value == null) return null;
        int count = value.codePointCount(0, value.length());
        if (count <= QUERY_PREVIEW_CODE_POINTS) return value;
        return value.substring(0, value.offsetByCodePoints(0, QUERY_PREVIEW_CODE_POINTS));
    }

    private static int byteLength(String value) {
        return value.getBytes(StandardCharsets.UTF_8).length;
    }

    private static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }

    private static String nonBlank(String value, String fallback) {
        return value != null && !value.isBlank() ? value : fallback;
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    private record BoundedJson(
            String json, int storedBytes, int originalBytes, boolean truncated,
            int omittedCount, String sha256) { }
}
