package com.coremasterkb.serving.api;

import com.coremasterkb.serving.structure.InspectService;
import com.coremasterkb.serving.structure.StructureNavigateService;
import com.coremasterkb.serving.structure.StructureToolException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A2 公共结构端点（39 号 §2.3；33 号「公共 UI REST 与 MCP internal 调同一
 * service」要求）：网页的大纲导航（父/子/上/下/祖先/后代）与节点 inspect。
 *
 * <p>与 MCP internal {@code /api/internal/navigate|inspect} 零复制——同一
 * {@link StructureNavigateService}/{@link InspectService}（同一 ref 授权解析
 * 与 typed error 契约），仅通道不同：网页走 {@code X-KB-User} 内网信任头 +
 * 显式 kbId 限定（与 {@code /api/v1/evidence} 同款）。</p>
 */
@RestController
@RequestMapping("/api/v1/structure")
public class StructureApiController {

    private static final Logger log = LoggerFactory.getLogger(StructureApiController.class);

    private final StructureNavigateService navigateService;
    private final InspectService inspectService;

    public StructureApiController(
            StructureNavigateService navigateService, InspectService inspectService) {
        this.navigateService = navigateService;
        this.inspectService = inspectService;
    }

    /**
     * st_ ref + 白名单关系导航（parent/children/previous/next/ancestors/
     * descendants/container/caption/footnotes/references）。
     */
    @GetMapping("/{ref}/navigate")
    public ResponseEntity<?> navigate(
            @PathVariable String ref,
            @RequestParam String relation,
            @RequestParam String domain,
            @RequestParam(required = false) String kbId,
            @RequestParam(required = false) Integer depth,
            @RequestParam(required = false) Integer limit,
            @RequestParam(required = false) String cursor,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        try {
            List<String> kbIds = kbId == null || kbId.isBlank() ? null : List.of(kbId);
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
        } catch (StructureToolException e) {
            log.warn("[structure-api] navigate ref={} relation={} code={}",
                    ref, relation, e.code());
            return ResponseEntity.status(e.status())
                    .body(Map.of("error", e.code(), "message", String.valueOf(e.getMessage())));
        }
    }

    /** st_ ref → 节点身份/能力/可用关系/表格 schema（网页表格查询面板的数据源）。 */
    @GetMapping("/{ref}/inspect")
    public ResponseEntity<?> inspect(
            @PathVariable String ref,
            @RequestParam String domain,
            @RequestParam(required = false) String kbId,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        try {
            List<String> kbIds = kbId == null || kbId.isBlank() ? null : List.of(kbId);
            InspectService.InspectResult result = inspectService.inspect(ref, domain, kbIds, kbUser);
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
        } catch (StructureToolException e) {
            log.warn("[structure-api] inspect ref={} code={}", ref, e.code());
            return ResponseEntity.status(e.status())
                    .body(Map.of("error", e.code(), "message", String.valueOf(e.getMessage())));
        }
    }
}
