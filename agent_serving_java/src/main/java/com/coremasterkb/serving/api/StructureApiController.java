package com.coremasterkb.serving.api;

import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.structure.InspectService;
import com.coremasterkb.serving.structure.StructureNavigateService;
import com.coremasterkb.serving.structure.StructureQueryDsl;
import com.coremasterkb.serving.structure.StructureToolException;
import com.coremasterkb.serving.structure.StructuredQueryService;
import com.fasterxml.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Supplier;

/**
 * A2/A3 公共结构端点（39 号 §2.3/§3.3；33 号「公共 UI REST 与 MCP internal
 * 调同一 service」要求）：网页的大纲导航（父/子/上/下/祖先/后代）、节点
 * inspect 与表格精确查询。
 *
 * <p>与 MCP internal {@code /api/internal/navigate|inspect|structured-query}
 * 零复制——同一 {@link StructureNavigateService}/{@link InspectService}/
 * {@link StructuredQueryService}（同一 ref 授权解析与 typed error 契约），
 * 仅通道不同：网页走 {@code X-KB-User} 内网信任头 + 显式 kbId 限定。</p>
 *
 * <p><b>P1-5</b>：所有端点在 service 调用期间设置 {@link DomainContext}
 * （finally 清理）——与 InternalStructureController 同款；非默认域不再
 * 走默认 DataSource 串库。</p>
 *
 * <p><b>P1-6 契约（唯一正式契约）</b>：</p>
 * <ul>
 *   <li>{@code POST /api/v1/structure/query}——body：
 *       {@code {ref, query{...}, domain, kbId?}}。ref 不进 URL path
 *       （内部 ref 含 {@code #}/{@code /}——fragment 截断与 path 编码陷阱）；</li>
 *   <li>{@code GET /api/v1/structure/navigate?ref=...&relation=...&domain=...&kbId=...}；</li>
 *   <li>{@code GET /api/v1/structure/inspect?ref=...&domain=...&kbId=...}。</li>
 * </ul>
 */
@RestController
@RequestMapping("/api/v1/structure")
public class StructureApiController {

    private static final Logger log = LoggerFactory.getLogger(StructureApiController.class);

    private final StructureNavigateService navigateService;
    private final InspectService inspectService;
    private final StructuredQueryService queryService;

    public StructureApiController(
            StructureNavigateService navigateService, InspectService inspectService,
            StructuredQueryService queryService) {
        this.navigateService = navigateService;
        this.inspectService = inspectService;
        this.queryService = queryService;
    }

    /** st_/内部 ref + 白名单关系导航（parent/children/previous/next/…）。 */
    @GetMapping("/navigate")
    public ResponseEntity<?> navigate(
            @RequestParam String ref,
            @RequestParam String relation,
            @RequestParam String domain,
            @RequestParam(required = false) String kbId,
            @RequestParam(required = false) Integer depth,
            @RequestParam(required = false) Integer limit,
            @RequestParam(required = false) String cursor,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        return navigateQuerySafe(ref, relation, domain, kbId, depth, limit, cursor, kbUser);
    }

    /** 表格资产 ref（st_ 或内部 "{doc}#table:{t}"）+ schema-bound DSL. */
    @PostMapping("/query")
    public ResponseEntity<?> query(
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        return queryBodySafe(
                text(body, "ref"), body == null ? null : body.get("query"),
                text(body, "domain"), text(body, "kbId"), kbUser);
    }

    /** ref → 节点身份/能力/可用关系/表格 schema（网页表格查询面板的数据源）。 */
    @GetMapping("/inspect")
    public ResponseEntity<?> inspect(
            @RequestParam String ref,
            @RequestParam String domain,
            @RequestParam(required = false) String kbId,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        return inspectQuerySafe(ref, domain, kbId, kbUser);
    }

    // ------------------------------------------------------------------ 域包装 + 端点体

    /** P1-5/P1-6：navigate 端点体（DomainContext 包裹；测试直调）。 */
    ResponseEntity<?> navigateQuerySafe(String ref, String relation, String domain,
                                        String kbId, Integer depth, Integer limit,
                                        String cursor, String kbUser) {
        try {
            List<String> kbIds = kbIdsOf(kbId);
            return withDomain(domain, () -> {
                StructureNavigateService.NavigateResult result = navigateService.navigate(
                        ref, relation, depth, limit, cursor, domain, kbIds, kbUser);
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
        } catch (StructureToolException e) {
            return typed(e, "navigate", ref);
        }
    }

    /** P1-5/P1-6：query 端点体（DomainContext 包裹；测试直调）。 */
    ResponseEntity<?> queryBodySafe(String ref, JsonNode queryNode, String domain,
                                    String kbId, String kbUser) {
        try {
            if (ref == null || ref.isBlank()) {
                return ResponseEntity.badRequest().body(Map.of(
                        "error", "invalid_ref", "message", "ref 必填"));
            }
            List<String> kbIds = kbIdsOf(kbId);
            return withDomain(domain, () -> {
                StructuredQueryService.QuerySpec spec =
                        StructureQueryDsl.parseSpec(queryNode);
                StructuredQueryService.QueryResult result =
                        queryService.query(ref, spec, domain, kbIds, kbUser);
                Map<String, Object> out = new LinkedHashMap<>();
                out.put("asset_ref", result.asset_ref());
                out.put("table_name", result.table_name());
                out.put("columns", result.columns());
                out.put("rows", result.rows());
                out.put("cursor", result.cursor());
                out.put("has_more", result.has_more());
                out.put("aggregate", result.aggregate());
                return ResponseEntity.ok(out);
            });
        } catch (StructureToolException e) {
            return typed(e, "query", ref);
        } catch (IllegalArgumentException e) {
            // DSL 白名单键（29 号 2.9）：typed 400，不静默当空条件
            log.warn("[structure-api] query ref={} invalid dsl: {}", ref, e.getMessage());
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "invalid_query", "message", String.valueOf(e.getMessage())));
        }
    }

    /** P1-5/P1-6：inspect 端点体（DomainContext 包裹；测试直调）。 */
    ResponseEntity<?> inspectQuerySafe(String ref, String domain, String kbId,
                                       String kbUser) {
        try {
            List<String> kbIds = kbIdsOf(kbId);
            return withDomain(domain, () -> {
                InspectService.InspectResult result = inspectService.inspect(
                        ref, domain, kbIds, kbUser);
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
        } catch (StructureToolException e) {
            return typed(e, "inspect", ref);
        }
    }

    // ------------------------------------------------------------------ plumbing

    /** P1-5：DomainContext set → call → finally clear（线程复用不串域）。 */
    private <T> T withDomain(String domain, Supplier<T> call) {
        DomainContext.set(domain);
        try {
            return call.get();
        } finally {
            DomainContext.clear();
        }
    }

    private static List<String> kbIdsOf(String kbId) {
        return kbId == null || kbId.isBlank() ? null : List.of(kbId);
    }

    private ResponseEntity<?> typed(StructureToolException e, String op, String ref) {
        log.warn("[structure-api] {} ref={} code={}", op, ref, e.code());
        return ResponseEntity.status(e.status())
                .body(Map.of("error", e.code(), "message", String.valueOf(e.getMessage())));
    }

    private static String text(JsonNode body, String field) {
        if (body == null) {
            return null;
        }
        JsonNode v = body.get(field);
        return v != null && v.isTextual() ? v.asText() : null;
    }
}
