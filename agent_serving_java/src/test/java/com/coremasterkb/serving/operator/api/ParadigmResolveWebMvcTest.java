package com.coremasterkb.serving.operator.api;

import com.coremasterkb.serving.operator.paradigm.ParadigmCatalogService;
import com.coremasterkb.serving.operator.paradigm.ParadigmEntity;
import com.coremasterkb.serving.operator.paradigm.ParadigmNotFoundException;
import com.coremasterkb.serving.operator.paradigm.ParadigmService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Web-layer contract for the MCP auto-match lookup.
 *
 * <p>Standalone MockMvc, matching {@code SearchControllerWebMvcTest}: the application class carries
 * {@code @MapperScan}, so a {@code @WebMvcTest} slice would drag in every mapper and fail on a
 * missing sqlSessionFactory.</p>
 *
 * <p>The routing case below is the point of this class. {@code /resolve} and {@code /{id}} are both
 * registered under {@code /api/v1/paradigm}; that a literal segment outranks a path variable is
 * Spring behaviour no compiler checks, and getting it wrong would turn every MCP lookup into a
 * {@code paradigm_not_found} for a paradigm literally named "resolve".</p>
 */
@DisplayName("Paradigm resolve endpoint")
class ParadigmResolveWebMvcTest {

    private MockMvc mockMvc;
    private ParadigmService paradigmService;

    @BeforeEach
    void setUp() {
        paradigmService = mock(ParadigmService.class);
        mockMvc = MockMvcBuilders
                .standaloneSetup(new ParadigmController(
                        paradigmService,
                        mock(ParadigmCatalogService.class),
                        mock(ParadigmExecutionService.class)))
                .setControllerAdvice(new OperatorExceptionHandler(
                        new com.coremasterkb.serving.api.GlobalExceptionHandler()))
                .build();
    }

    @Test
    @DisplayName("bound domain returns the paradigm, its call url, and the resolution source")
    void boundDomain() throws Exception {
        ParadigmEntity e = new ParadigmEntity();
        e.setId("pd-abc");
        e.setName("odn-production");
        e.setDescription("ODN 生产检索链");
        e.setCurrentVersion(3);
        when(paradigmService.resolveFor("odn", null)).thenReturn(
                new ParadigmService.Resolution(e, "official", null));

        mockMvc.perform(get("/api/v1/paradigm/resolve").param("domain", "odn"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.bound").value(true))
                .andExpect(jsonPath("$.domain").value("odn"))
                .andExpect(jsonPath("$.paradigmId").value("pd-abc"))
                .andExpect(jsonPath("$.name").value("odn-production"))
                .andExpect(jsonPath("$.version").value(3))
                .andExpect(jsonPath("$.source").value("official"))
                .andExpect(jsonPath("$.url").value("/api/v1/paradigm/pd-abc/search"));
    }

    @Test
    @DisplayName("library degradation is surfaced via degraded/degradedFrom")
    void libraryDegradationIsObservable() throws Exception {
        ParadigmEntity e = new ParadigmEntity();
        e.setId("pd-dom");
        e.setName("fallback");
        e.setCurrentVersion(1);
        when(paradigmService.resolveFor("odn", null)).thenReturn(
                new ParadigmService.Resolution(e, "official", "library"));

        mockMvc.perform(get("/api/v1/paradigm/resolve").param("domain", "odn"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.bound").value(true))
                .andExpect(jsonPath("$.degraded").value(true))
                .andExpect(jsonPath("$.degradedFrom").value("library"));
    }

    @Test
    @DisplayName("unbound domain is 200 with bound=false, never 404")
    void unboundDomainIsNotAnError() throws Exception {
        when(paradigmService.resolveFor("generic", null)).thenReturn(null);

        mockMvc.perform(get("/api/v1/paradigm/resolve").param("domain", "generic"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.bound").value(false))
                .andExpect(jsonPath("$.domain").value("generic"))
                .andExpect(jsonPath("$.paradigmId").doesNotExist());
    }

    @Test
    @DisplayName("/resolve is not swallowed by the /{id} route")
    void resolveOutranksThePathVariableRoute() throws Exception {
        when(paradigmService.resolveFor(anyString(), any())).thenReturn(null);
        // If /{id} won, this would hit getOrThrow("resolve") and 404 through the advice.
        when(paradigmService.getOrThrow("resolve"))
                .thenThrow(new ParadigmNotFoundException("paradigm not found: resolve"));

        mockMvc.perform(get("/api/v1/paradigm/resolve").param("domain", "odn"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.bound").exists());

        verify(paradigmService, never()).getOrThrow("resolve");
    }

    @Test
    @DisplayName("missing domain param is a 400, not a silent all-domain lookup")
    void missingDomainIsRejected() throws Exception {
        mockMvc.perform(get("/api/v1/paradigm/resolve"))
                .andExpect(status().isBadRequest());

        verify(paradigmService, never()).resolveFor(anyString(), any());
    }
}
