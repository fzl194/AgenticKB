package com.coremasterkb.serving.api;

import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.structure.InspectService;
import com.coremasterkb.serving.structure.StructureNavigateService;
import com.coremasterkb.serving.structure.StructureToolException;
import com.coremasterkb.serving.structure.StructuredQueryService;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * P1-5 回归：公共 Structure REST 必须设置 DomainContext（try/finally 清理）。
 *
 * <p>缺陷：navigate/inspect/query 三个端点直接调 service——DB 查询前没有
 * {@code DomainContext.set(domain)}，非默认域走默认 DataSource（串库）；
 * 且无 finally 清理，线程池复用会串域。</p>
 */
@DisplayName("StructureApiController DomainContext lifecycle")
class StructureApiControllerDomainContextTest {

    private final ObjectMapper mapper = new ObjectMapper();

    @AfterEach
    void cleanup() {
        DomainContext.clear();
    }

    /** 记录 service 执行瞬间 DomainContext 的值. */
    static final class ProbingNavigateService extends StructureNavigateService {
        String seenDomainDuringCall;
        RuntimeException raiseOnCall;

        ProbingNavigateService() {
            super(null, null, null, null);
        }

        @Override
        public NavigateResult navigate(String ref, String relation, Integer depth,
                                       Integer limit, String cursor, String domain,
                                       List<String> kbIds, String username) {
            seenDomainDuringCall = DomainContext.get();
            if (raiseOnCall != null) {
                throw raiseOnCall;
            }
            return new NavigateResult(ref, relation, 1, 10, List.of(), null, false, null);
        }
    }

    static final class ProbingInspectService extends InspectService {
        String seenDomainDuringCall;

        ProbingInspectService() {
            super(null, null, null, null, null);
        }

        @Override
        public InspectResult inspect(String ref, String domain, List<String> kbIds,
                                     String username) {
            seenDomainDuringCall = DomainContext.get();
            return new InspectResult(ref, "structure", "section", null, null,
                    java.util.Map.of(), List.of(), List.of());
        }
    }

    static final class ProbingQueryService extends StructuredQueryService {
        String seenDomainDuringCall;

        ProbingQueryService() {
            super(null, null);
        }

        @Override
        public QueryResult query(String assetRef, QuerySpec spec, String domain,
                                 List<String> kbIds, String username) {
            seenDomainDuringCall = DomainContext.get();
            return new QueryResult(assetRef, "t", List.of(), List.of(), null, false, null);
        }
    }

    private StructureApiController controller(
            ProbingNavigateService nav, ProbingInspectService insp, ProbingQueryService q) {
        return new StructureApiController(nav, insp, q);
    }

    @Test
    @DisplayName("navigate sets DomainContext during service call and clears after")
    void navigateSetsAndClearsDomainContext() {
        ProbingNavigateService nav = new ProbingNavigateService();
        var c = controller(nav, new ProbingInspectService(), new ProbingQueryService());

        ResponseEntity<?> out = c.navigateQuerySafe(
                "doc_x", "children", "civil_engineering", "kb-1", null, null, null, "user");

        assertThat(out.getStatusCode().value()).isEqualTo(200);
        assertThat(nav.seenDomainDuringCall).isEqualTo("civil_engineering");
        assertThat(DomainContext.get()).isNull(); // finally 清理
    }

    @Test
    @DisplayName("inspect sets DomainContext during service call")
    void inspectSetsDomainContext() {
        ProbingInspectService insp = new ProbingInspectService();
        var c = controller(new ProbingNavigateService(), insp, new ProbingQueryService());

        ResponseEntity<?> out = c.inspectQuerySafe("doc_x", "odn", "kb-1", "user");

        assertThat(out.getStatusCode().value()).isEqualTo(200);
        assertThat(insp.seenDomainDuringCall).isEqualTo("odn");
        assertThat(DomainContext.get()).isNull();
    }

    @Test
    @DisplayName("query sets DomainContext during service call")
    void querySetsDomainContext() {
        ProbingQueryService q = new ProbingQueryService();
        var c = controller(new ProbingNavigateService(), new ProbingInspectService(), q);
        JsonNode body = mapper.createObjectNode();

        ResponseEntity<?> out = c.queryBodySafe(
                "st_abc", body, "vendor_tech_docs", "kb-1", "user");

        assertThat(out.getStatusCode().value()).isEqualTo(200);
        assertThat(q.seenDomainDuringCall).isEqualTo("vendor_tech_docs");
        assertThat(DomainContext.get()).isNull();
    }

    @Test
    @DisplayName("DomainContext cleared even when service throws typed error")
    void domainContextClearedOnException() {
        ProbingNavigateService nav = new ProbingNavigateService();
        nav.raiseOnCall = new StructureToolException(
                "invalid_ref", HttpStatus.BAD_REQUEST, "bad", java.util.Map.of());
        var c = controller(nav, new ProbingInspectService(), new ProbingQueryService());

        ResponseEntity<?> out = c.navigateQuerySafe(
                "doc_x", "children", "cloud_core_network", "kb-1", null, null, null, "user");

        assertThat(out.getStatusCode().value()).isEqualTo(400); // typed error 不炸
        assertThat(DomainContext.get()).isNull(); // 异常路径也清理
    }

    @Test
    @DisplayName("consecutive requests to different domains never leak")
    void consecutiveDomainsDoNotLeak() {
        ProbingNavigateService nav = new ProbingNavigateService();
        var c = controller(nav, new ProbingInspectService(), new ProbingQueryService());

        c.navigateQuerySafe("doc_a", "children", "domain-a", "kb-1", null, null, null, "u");
        c.navigateQuerySafe("doc_b", "children", "domain-b", "kb-1", null, null, null, "u");

        // 第二次调用期间不得残留第一次的域（线程复用串域缺陷的直接复现）
        assertThat(nav.seenDomainDuringCall).isEqualTo("domain-b");
        assertThat(DomainContext.get()).isNull();
    }
}
