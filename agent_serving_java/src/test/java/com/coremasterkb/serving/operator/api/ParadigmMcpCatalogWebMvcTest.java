package com.coremasterkb.serving.operator.api;

import com.coremasterkb.serving.operator.paradigm.ParadigmCatalogService;
import com.coremasterkb.serving.operator.paradigm.ParadigmService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Web-layer contract for the MCP catalog endpoint.
 *
 * <p>Two things here are behaviour no compiler checks. First, routing: {@code /mcp-catalog} and
 * {@code /{id}} are both registered under {@code /api/v1/paradigm}, and a literal segment
 * outranking a path variable is Spring's pattern comparator, not something the code states — the
 * same trap {@code ParadigmResolveWebMvcTest} exists for. Second, disclosure: the {@code hidden}
 * block names knowledge bases and must never appear for an anonymous caller.</p>
 */
@DisplayName("Paradigm MCP catalog endpoint")
class ParadigmMcpCatalogWebMvcTest {

    private MockMvc mockMvc;
    private ParadigmService paradigmService;
    private ParadigmCatalogService catalogService;

    private static final ParadigmCatalogService.Catalog CATALOG = new ParadigmCatalogService.Catalog(
            List.of(new ParadigmCatalogService.Entry(
                    "pd-abc", "ODN 拓扑排障", "查 ODN 拓扑与端口", 3)),
            List.of(new ParadigmCatalogService.Hidden(
                    "pd-xyz", "内部资料检索", "not_servable",
                    List.of(), 0)));

    @BeforeEach
    void setUp() {
        paradigmService = mock(ParadigmService.class);
        catalogService = mock(ParadigmCatalogService.class);
        when(catalogService.build(any(), any())).thenReturn(CATALOG);
        mockMvc = MockMvcBuilders
                .standaloneSetup(new ParadigmController(
                        paradigmService,
                        catalogService,
                        mock(ParadigmExecutionService.class)))
                .setControllerAdvice(new OperatorExceptionHandler())
                .build();
    }

    @Test
    @DisplayName("routes to the catalog, not to /{id} with id=\"mcp-catalog\"")
    void literalSegmentOutranksPathVariable() throws Exception {
        mockMvc.perform(get("/api/v1/paradigm/mcp-catalog"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.paradigms[0].id").value("pd-abc"));

        verify(catalogService).build(isNull(), isNull());
        verify(paradigmService, never()).getOrThrow(anyString());
    }

    @Test
    @DisplayName("anonymous caller gets paradigms but no hidden block")
    void anonymousGetsNoHidden() throws Exception {
        mockMvc.perform(get("/api/v1/paradigm/mcp-catalog"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.paradigms").isArray())
                .andExpect(jsonPath("$.hidden").doesNotExist());
    }

    @Test
    @DisplayName("identified caller gets hidden with its reasons (publish-quality only)")
    void identifiedCallerGetsHidden() throws Exception {
        mockMvc.perform(get("/api/v1/paradigm/mcp-catalog").header("X-KB-User", "admin"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.hidden[0].id").value("pd-xyz"))
                .andExpect(jsonPath("$.hidden[0].reason").value("not_servable"));

        verify(catalogService).build(isNull(), eq("admin"));
    }

    @Test
    @DisplayName("a blank X-KB-User is treated as anonymous, not as a user named \"\"")
    void blankHeaderIsAnonymous() throws Exception {
        mockMvc.perform(get("/api/v1/paradigm/mcp-catalog").header("X-KB-User", "   "))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.hidden").doesNotExist());
    }

    @Test
    @DisplayName("domain filter reaches the service")
    void domainFilterIsPassedThrough() throws Exception {
        mockMvc.perform(get("/api/v1/paradigm/mcp-catalog").param("domain", "odn"))
                .andExpect(status().isOk());

        verify(catalogService).build(eq("odn"), isNull());
    }

    @Test
    @DisplayName("the entry carries what an agent needs to choose: name, description, version")
    void entryShapeIsAgentFacing() throws Exception {
        mockMvc.perform(get("/api/v1/paradigm/mcp-catalog"))
                .andExpect(jsonPath("$.paradigms[0].name").value("ODN 拓扑排障"))
                .andExpect(jsonPath("$.paradigms[0].description").value("查 ODN 拓扑与端口"))
                .andExpect(jsonPath("$.paradigms[0].version").value(3));
    }
}
