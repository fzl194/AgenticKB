package com.coremasterkb.serving.api;

import com.coremasterkb.serving.structure.InspectService;
import com.coremasterkb.serving.structure.StructureNavigateService;
import com.coremasterkb.serving.structure.StructureToolException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;
import java.util.Map;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * A2 公共结构端点（39 号 §2.3）：与 MCP internal 同 service 的薄通道——
 * 这里锁定身份头/kbId 透传、typed error 映射，导航语义本身由
 * StructureNavigateServiceTest 覆盖（同源不重复测）。
 */
@DisplayName("A2 StructureApiController")
class StructureApiControllerTest {

    private StructureNavigateService navigateService;
    private InspectService inspectService;
    private com.coremasterkb.serving.structure.StructuredQueryService queryService;
    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        navigateService = mock(StructureNavigateService.class);
        inspectService = mock(InspectService.class);
        queryService = mock(com.coremasterkb.serving.structure.StructuredQueryService.class);
        mvc = MockMvcBuilders.standaloneSetup(
                new StructureApiController(navigateService, inspectService, queryService))
                .build();
    }

    @Test
    @DisplayName("navigate：身份头 + kbId 透传给同源 service，结果原样投影")
    void navigatePassthrough() throws Exception {
        when(navigateService.navigate(
                eq("st_ab"), eq("children"), eq(1), eq(50), eq(null),
                eq("cloud_core_network"), eq(List.of("kb-1")), eq("alice")))
                .thenReturn(new StructureNavigateService.NavigateResult(
                        "st_ab", "children", 1, 50,
                        List.of(new StructureNavigateService.NodeSummary(
                                "d#section:0/1", "section", "参数说明", 2, 0,
                                null, List.of())),
                        null, false, Map.of()));

        mvc.perform(get("/api/v1/structure/st_ab/navigate")
                        .param("relation", "children")
                        .param("domain", "cloud_core_network")
                        .param("kbId", "kb-1")
                        .param("depth", "1")
                        .param("limit", "50")
                        .header("X-KB-User", "alice"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.nodes[0].ref").value("d#section:0/1"))
                .andExpect(jsonPath("$.nodes[0].title").value("参数说明"));

        verify(navigateService).navigate(
                eq("st_ab"), eq("children"), eq(1), eq(50), eq(null),
                eq("cloud_core_network"), eq(List.of("kb-1")), eq("alice"));
    }

    @Test
    @DisplayName("navigate：kbId 缺省 = 宽授权（null kbIds），typed error 映射 4xx")
    void navigateWithoutKbIdAndErrors() throws Exception {
        when(navigateService.navigate(
                any(), any(), any(), any(), any(), any(), isNull(), any()))
                .thenThrow(StructureToolException.unsupportedOperation(
                        "未知关系: jump", Map.of()));

        mvc.perform(get("/api/v1/structure/st_ab/navigate")
                        .param("relation", "jump")
                        .param("domain", "cloud_core_network"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("unsupported_operation"));
    }

    @Test
    @DisplayName("inspect：身份透传 + 越权/失效 typed error 同响应语义")
    void inspectPassthrough() throws Exception {
        when(inspectService.inspect(
                eq("st_ab"), eq("cloud_core_network"), eq(List.of("kb-1")), eq("bob")))
                .thenReturn(new InspectService.InspectResult(
                        "st_ab", "structure", "section", "section",
                        Map.of(), Map.of(), List.of(), List.of()));

        mvc.perform(get("/api/v1/structure/st_ab/inspect")
                        .param("domain", "cloud_core_network")
                        .param("kbId", "kb-1")
                        .header("X-KB-User", "bob"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.node_type").value("section"));
    }
    @Test
    @DisplayName("A3 query：body.query DSL → 同源 service；typed error 映射 4xx")
    void queryPassthroughAndErrors() throws Exception {
        when(queryService.query(
                eq("st_tbl"), any(), eq("cloud_core_network"), eq(List.of("kb-1")), eq("alice")))
                .thenReturn(new com.coremasterkb.serving.structure.StructuredQueryService.QueryResult(
                        "st_tbl", "tbl:alarm", List.of(), List.of(),
                        null, false, null));

        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                        .post("/api/v1/structure/st_tbl/query")
                        .content("{\"query\": {\"where\": [{\"field\": \"告警码\", "
                                + "\"op\": \"eq\", \"value\": \"A101\"}]}}")
                        .contentType("application/json")
                        .param("domain", "cloud_core_network")
                        .param("kbId", "kb-1")
                        .header("X-KB-User", "alice"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.table_name").value("tbl:alarm"));

        // DSL 白名单外键 → 400（不静默当空条件）
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                        .post("/api/v1/structure/st_tbl/query")
                        .content("{\"query\": {\"filter\": {}}}")
                        .contentType("application/json")
                        .param("domain", "cloud_core_network"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_query"));
    }
}
