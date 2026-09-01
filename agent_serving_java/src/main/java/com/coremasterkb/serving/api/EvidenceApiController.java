package com.coremasterkb.serving.api;

import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.structure.EvidenceToolService;
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

import java.util.List;
import java.util.Map;

/**
 * 对外证据端点（2026-09-01）：前端检索面板把截断证据展开为完整原文。
 *
 * <p>与 MCP 的 get_content(ev_) 同源（{@link EvidenceToolService#getEvidence}，
 * 含 ref 反查授权），但走平台前端通道：{@code X-KB-User} 内网信任头 + 显式
 * kb_id 限定授权范围——前端知道检索发生在哪个库，不传即宽检索（域内
 * 该用户可见的快照）。typed error 转结构化 4xx，与 MCP 工具面的稳定
 * code 契约一致。</p>
 */
@RestController
@RequestMapping("/api/v1/evidence")
public class EvidenceApiController {

    private static final Logger log = LoggerFactory.getLogger(EvidenceApiController.class);

    private final EvidenceToolService evidenceToolService;

    public EvidenceApiController(EvidenceToolService evidenceToolService) {
        this.evidenceToolService = evidenceToolService;
    }

    /** ev_ ref → 完整/更大粒度原文（mode 缺省 auto=预算内就大）。 */
    @GetMapping("/{ref}")
    public ResponseEntity<?> evidence(
            @PathVariable String ref,
            @RequestParam String domain,
            @RequestParam(required = false) String kbId,
            @RequestParam(required = false) String mode,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        try {
            List<String> kbIds = kbId == null || kbId.isBlank() ? null : List.of(kbId);
            EvidenceResponse.EvidenceItem item =
                    evidenceToolService.getEvidence(ref, mode, domain, kbIds, kbUser);
            return ResponseEntity.ok(item);
        } catch (StructureToolException e) {
            log.warn("[evidence-api] ref={} code={}", ref, e.code());
            return ResponseEntity.status(e.status())
                    .body(Map.of("error", e.code(), "message", String.valueOf(e.getMessage())));
        }
    }
}
