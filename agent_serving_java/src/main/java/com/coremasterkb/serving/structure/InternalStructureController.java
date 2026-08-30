package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.structure.StructuredQueryService.Aggregate;
import com.coremasterkb.serving.structure.StructuredQueryService.OrderClause;
import com.coremasterkb.serving.structure.StructuredQueryService.QuerySpec;
import com.coremasterkb.serving.structure.StructuredQueryService.WhereClause;
import com.fasterxml.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 结构工具族 internal REST（批次8 R7，25 号 §6.10/§6.11/§8.1）。
 *
 * <p>仅供 mcp_server 容器转发调用（对齐 mining 批次7 X-Internal-Auth 模式）：mcp_server 已按
 * 用户密钥验明 username，本组端点信任该身份并做<b>资源级授权</b>（ref 反查仅在请求
 * kb_ids ∩ 用户开放库 的 snapshot 集内匹配）。密钥经
 * {@code serving.internal-auth.secret}（env {@code SERVING_INTERNAL_AUTH_SECRET}）注入；
 * 未配置 = 端点整体 503（拒绝服务而非无鉴权放行）。比较用常时 MessageDigest.isEqual。</p>
 *
 * <p>所有 DB 访问在 {@link DomainContext} 域路由下执行（与检索链一致）。</p>
 */
@RestController
@RequestMapping("/api/internal")
public class InternalStructureController {

    private static final Logger log = LoggerFactory.getLogger(InternalStructureController.class);

    private final ServingProperties properties;
    private final EvidenceToolService evidenceToolService;
    private final InspectService inspectService;
    private final StructureNavigateService navigateService;
    private final StructuredQueryService queryService;

    public InternalStructureController(ServingProperties properties,
                                       EvidenceToolService evidenceToolService,
                                       InspectService inspectService,
                                       StructureNavigateService navigateService,
                                       StructuredQueryService queryService) {
        this.properties = properties;
        this.evidenceToolService = evidenceToolService;
        this.inspectService = inspectService;
        this.navigateService = navigateService;
        this.queryService = queryService;
    }

    // ------------------------------------------------------------------ endpoints

    /** ev_ ref → 完整/更大粒度原文。body: {domain, kb_ids, username, mode?} */
    @PostMapping("/evidence/{ref}")
    public ResponseEntity<?> evidence(
            @PathVariable String ref,
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-Internal-Auth", required = false) String internalAuth) {
        return withAuth(internalAuth, body, (domain, kbIds, username) ->
                ResponseEntity.ok(evidenceToolService.getEvidence(
                        ref, text(body, "mode"), domain, kbIds, username)));
    }

    /** doc_ ref → 结构化章节（有界稳定分页）。body: {domain, kb_ids, username, limit?, cursor?} */
    @PostMapping("/document/{ref}")
    public ResponseEntity<?> document(
            @PathVariable String ref,
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-Internal-Auth", required = false) String internalAuth) {
        return withAuth(internalAuth, body, (domain, kbIds, username) -> {
            EvidenceToolService.DocumentResult result = evidenceToolService.getDocument(
                    ref, intOrNull(body.get("limit")), text(body, "cursor"),
                    domain, kbIds, username);
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("document_ref", result.document_ref());
            out.put("source", result.source());
            out.put("sections", result.sections());
            out.put("segments", result.segments());
            out.put("total_segments", result.total_segments());
            out.put("cursor", result.cursor());
            out.put("has_more", result.has_more());
            return ResponseEntity.ok(out);
        });
    }

    /** 任意 ref → capabilities/schema/relations。body: {ref, domain, kb_ids, username} */
    @PostMapping("/inspect")
    public ResponseEntity<?> inspect(
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-Internal-Auth", required = false) String internalAuth) {
        return withAuth(internalAuth, body, (domain, kbIds, username) -> {
            InspectService.InspectResult result =
                    inspectService.inspect(requiredRef(body), domain, kbIds, username);
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("ref", result.ref());
            out.put("ref_kind", result.ref_kind());
            out.put("node_type", result.node_type());
            if (result.evidence_type() != null) {
                out.put("evidence_type", result.evidence_type());
            }
            out.put("source", result.source());
            out.put("capabilities", result.capabilities());
            out.put("relations", result.relations());
            out.put("assets", result.assets());
            return ResponseEntity.ok(out);
        });
    }

    /** st_ ref + 白名单关系导航。body: {ref, relation, depth?, limit?, cursor?, domain, kb_ids, username} */
    @PostMapping("/navigate")
    public ResponseEntity<?> navigate(
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-Internal-Auth", required = false) String internalAuth) {
        return withAuth(internalAuth, body, (domain, kbIds, username) -> {
            StructureNavigateService.NavigateResult result = navigateService.navigate(
                    requiredRef(body), text(body, "relation"),
                    intOrNull(body.get("depth")), intOrNull(body.get("limit")),
                    text(body, "cursor"), domain, kbIds, username);
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("structure_ref", result.structure_ref());
            out.put("relation", result.relation());
            out.put("depth", result.depth());
            out.put("limit", result.limit());
            out.put("nodes", result.nodes());
            out.put("cursor", result.cursor());
            out.put("has_more", result.has_more());
            out.put("source", result.source());
            return ResponseEntity.ok(out);
        });
    }

    /** st_ asset ref + schema-bound DSL。body: {ref, query{select/where/order_by/limit/cursor/aggregate}, domain, kb_ids, username} */
    @PostMapping("/structured-query")
    public ResponseEntity<?> structuredQuery(
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-Internal-Auth", required = false) String internalAuth) {
        return withAuth(internalAuth, body, (domain, kbIds, username) -> {
            JsonNode q = body.get("query");
            QuerySpec spec = parseSpec(q == null ? null : q);
            StructuredQueryService.QueryResult result =
                    queryService.query(requiredRef(body), spec, domain, kbIds, username);
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("asset_ref", result.asset_ref());
            out.put("table_name", result.table_name());
            out.put("columns", result.columns());
            if (result.aggregate() != null) {
                out.put("aggregate", result.aggregate());
            } else {
                out.put("rows", result.rows());
                out.put("cursor", result.cursor());
                out.put("has_more", result.has_more());
            }
            return ResponseEntity.ok(out);
        });
    }

    // ------------------------------------------------------------------ auth + plumbing

    private interface AuthedCall {
        ResponseEntity<?> apply(String domain, List<String> kbIds, String username);
    }

    private ResponseEntity<?> withAuth(String internalAuth, JsonNode body,
                                       AuthedCall call) {
        String expected = properties.internalAuth() != null
                ? properties.internalAuth().secret() : "";
        if (expected == null || expected.isBlank()) {
            log.warn("[internal-api] serving.internal-auth.secret 未配置——端点拒绝服务（503）");
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Map.of("error", "internal_auth_not_configured"));
        }
        if (internalAuth == null || !MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8),
                internalAuth.getBytes(StandardCharsets.UTF_8))) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "unauthenticated"));
        }

        String domain = text(body, "domain");
        String username = text(body, "username");
        List<String> kbIds = stringList(body, "kb_ids");
        DomainContext.set(domain);
        try {
            return call.apply(domain, kbIds, username);
        } finally {
            DomainContext.clear();
        }
    }

    @ExceptionHandler(StructureToolException.class)
    public ResponseEntity<?> handleTyped(StructureToolException ex) {
        Map<String, Object> error = new LinkedHashMap<>();
        error.put("code", ex.code());
        error.put("message", ex.getMessage());
        if (!ex.details().isEmpty()) {
            error.put("details", ex.details());
        }
        return ResponseEntity.status(ex.status()).body(Map.of("error", error));
    }

    // ------------------------------------------------------------------ body parsing

    private static QuerySpec parseSpec(JsonNode q) {
        if (q == null || q.isNull()) {
            return new QuerySpec(null, null, null, null, null, null);
        }
        // 29号 2.9：query 下只认白名单键——未知键（如误用 filter/orderBy）
        // 显式 400，不再静默当成空条件返回全表（调用方以为过滤生效）。
        java.util.Set<String> fieldNames = new java.util.HashSet<>();
        q.fieldNames().forEachRemaining(fieldNames::add);
        fieldNames.removeAll(java.util.Set.of(
                "select", "where", "order_by", "limit", "cursor", "aggregate"));
        if (!fieldNames.isEmpty()) {
            throw new IllegalArgumentException(
                    "unsupported_query_key:" + String.join(",", fieldNames));
        }
        List<String> select = stringList(q, "select");
        List<WhereClause> where = new ArrayList<>();
        JsonNode whereNode = q.get("where");
        if (whereNode != null && whereNode.isArray()) {
            for (JsonNode w : whereNode) {
                where.add(new WhereClause(text(w, "field"), text(w, "op"), w.get("value")));
            }
        }
        List<OrderClause> orderBy = new ArrayList<>();
        JsonNode orderNode = q.get("order_by");
        if (orderNode != null && orderNode.isArray()) {
            for (JsonNode o : orderNode) {
                orderBy.add(new OrderClause(text(o, "field"), text(o, "direction")));
            }
        }
        Aggregate aggregate = null;
        JsonNode agg = q.get("aggregate");
        if (agg != null && agg.isObject() && agg.hasNonNull("op")) {
            aggregate = new Aggregate(text(agg, "op"), text(agg, "field"));
        }
        return new QuerySpec(select, where, orderBy, intOrNull(q.get("limit")),
                text(q, "cursor"), aggregate);
    }

    private static String requiredRef(JsonNode body) {
        String ref = text(body, "ref");
        if (ref == null) {
            throw StructureToolException.invalidRef("ref 必填");
        }
        return ref;
    }

    private static String text(JsonNode body, String field) {
        if (body == null) return null;
        JsonNode v = body.get(field);
        return (v != null && v.isTextual() && !v.asText().isBlank()) ? v.asText() : null;
    }

    private static Integer intOrNull(JsonNode v) {
        return (v != null && v.isNumber()) ? v.asInt() : null;
    }

    private static List<String> stringList(JsonNode body, String field) {
        if (body == null) return List.of();
        JsonNode v = body.get(field);
        if (v == null || !v.isArray()) return List.of();
        List<String> out = new ArrayList<>();
        for (JsonNode item : v) {
            if (item != null && item.isTextual() && !item.asText().isBlank()) {
                out.add(item.asText());
            }
        }
        return out;
    }

}
