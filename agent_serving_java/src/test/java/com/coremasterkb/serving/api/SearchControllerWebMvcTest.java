package com.coremasterkb.serving.api;

import com.coremasterkb.serving.application.SearchService;
import com.coremasterkb.serving.domain.ContextPack;
import com.coremasterkb.serving.domain.SearchRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Web-layer contract for {@code /api/v1/search}, with the service mocked.
 *
 * <p>Deliberately NOT a {@code pg-integration} test: {@link SearchControllerTest} extends
 * {@code AbstractPgIntegrationTest} and skips wholesale whenever the domain has no active
 * release, which is most of the time. Everything asserted here — that the identity header binds,
 * that {@code kbIds} survives JSON binding, that error codes become the right HTTP status through
 * the real advice — has no compile-time guarantee and would otherwise be verified by nothing.</p>
 *
 * <p>Standalone MockMvc rather than {@code @WebMvcTest}: the application class carries
 * {@code @MapperScan}, so the web slice drags every MyBatis mapper into the context and fails on
 * a missing {@code sqlSessionFactory}. Standalone setup exercises the same three things — argument
 * binding, message conversion, and the registered advice — without loading a context at all.</p>
 */
@DisplayName("SearchController web layer")
class SearchControllerWebMvcTest {

    private static final String BODY_PLAIN =
            "{\"query\":\"SMF配置\",\"domain\":\"cloud_core_network\"}";

    private MockMvc mockMvc;
    private SearchService searchService;

    @BeforeEach
    void setUp() {
        searchService = mock(SearchService.class);
        mockMvc = MockMvcBuilders
                .standaloneSetup(new SearchController(searchService))
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    private static ContextPack emptyPack() {
        return new ContextPack(null, List.of(), List.of(), List.of(),
                List.of(), List.of(), List.of(), Map.of());
    }

    private ArgumentCaptor<SearchRequest> stubOk() {
        when(searchService.search(any(), any())).thenReturn(emptyPack());
        return ArgumentCaptor.forClass(SearchRequest.class);
    }

    // ------------------------------------------------------------------ identity header

    @Test
    @DisplayName("X-KB-User binds and reaches the service")
    void identityHeaderBinds() throws Exception {
        stubOk();

        mockMvc.perform(post("/api/v1/search")
                        .header("X-KB-User", "alice")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY_PLAIN))
                .andExpect(status().isOk());

        verify(searchService).search(any(SearchRequest.class), eq("alice"));
    }

    @Test
    @DisplayName("no X-KB-User header — the request still works, as anonymous")
    void missingIdentityHeaderIsAnonymous() throws Exception {
        // mcp_server and every pre-existing client send no identity header at all; making the
        // header required would break them.
        stubOk();

        mockMvc.perform(post("/api/v1/search")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY_PLAIN))
                .andExpect(status().isOk());

        verify(searchService).search(any(SearchRequest.class), isNull());
    }

    // ------------------------------------------------------------------ kbIds binding

    @Test
    @DisplayName("kbIds binds from the JSON body")
    void kbIdsBindsCamelCase() throws Exception {
        ArgumentCaptor<SearchRequest> captor = stubOk();

        mockMvc.perform(post("/api/v1/search")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"query\":\"q\",\"domain\":\"d\",\"kbIds\":[\"kb1\",\"kb2\"]}"))
                .andExpect(status().isOk());

        verify(searchService).search(captor.capture(), any());
        assertThat(captor.getValue().kbIds()).containsExactly("kb1", "kb2");
    }

    @Test
    @DisplayName("kb_ids also binds, matching the snake_case response style")
    void kbIdsBindsSnakeCase() throws Exception {
        ArgumentCaptor<SearchRequest> captor = stubOk();

        mockMvc.perform(post("/api/v1/search")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"query\":\"q\",\"domain\":\"d\",\"kb_ids\":[\"kb1\"]}"))
                .andExpect(status().isOk());

        verify(searchService).search(captor.capture(), any());
        assertThat(captor.getValue().kbIds()).containsExactly("kb1");
    }

    @Test
    @DisplayName("a request without kbIds arrives with an empty list, not null")
    void absentKbIdsIsEmpty() throws Exception {
        ArgumentCaptor<SearchRequest> captor = stubOk();

        mockMvc.perform(post("/api/v1/search")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY_PLAIN))
                .andExpect(status().isOk());

        verify(searchService).search(captor.capture(), any());
        assertThat(captor.getValue().kbIds()).isEmpty();
    }

    // ------------------------------------------------------------------ error surface

    @Test
    @DisplayName("kb_not_found surfaces as 404 through the advice")
    void kbNotFoundBecomes404() throws Exception {
        when(searchService.search(any(), any()))
                .thenThrow(new IllegalArgumentException("kb_not_found"));

        mockMvc.perform(post("/api/v1/search")
                        .header("X-KB-User", "mallory")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"query\":\"q\",\"domain\":\"d\",\"kbIds\":[\"kb1\"]}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("kb_not_found"));
    }

    @Test
    @DisplayName("no_active_kb_build surfaces as 404")
    void noActiveKbBuildBecomes404() throws Exception {
        when(searchService.search(any(), any()))
                .thenThrow(new IllegalArgumentException("no_active_kb_build"));

        mockMvc.perform(post("/api/v1/search")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"query\":\"q\",\"domain\":\"d\",\"kbIds\":[\"kb1\"]}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("no_active_kb_build"));
    }

    @Test
    @DisplayName("no_active_release still maps to 503 — the pre-existing path is untouched")
    void noActiveReleaseStillMapsTo503() throws Exception {
        when(searchService.search(any(), any()))
                .thenThrow(new IllegalArgumentException("no_active_release"));

        mockMvc.perform(post("/api/v1/search")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY_PLAIN))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("no_active_release"));
    }
}
