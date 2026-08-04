package com.coremasterkb.serving.api;

import com.coremasterkb.serving.application.FullTextService;
import com.coremasterkb.serving.domain.FullTextRequest;
import com.coremasterkb.serving.domain.FullTextResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Web-layer contract for {@code /api/v1/segments/fulltext}, with the service mocked.
 *
 * <p>Standalone MockMvc rather than {@code @WebMvcTest} for the same reason as
 * {@link SearchControllerWebMvcTest}: the application class carries {@code @MapperScan}, so the web
 * slice would drag every MyBatis mapper in and fail on a missing {@code sqlSessionFactory}.</p>
 *
 * <p>What is asserted here has no compile-time guarantee: that the identity header binds, that the
 * snake_case aliases survive JSON binding, and that the new error codes reach the right HTTP status
 * through the real advice.</p>
 */
@DisplayName("FullTextController web layer")
class FullTextWebMvcTest {

    private MockMvc mockMvc;
    private FullTextService fullTextService;

    @BeforeEach
    void setUp() {
        fullTextService = mock(FullTextService.class);
        mockMvc = MockMvcBuilders
                .standaloneSetup(new FullTextController(fullTextService))
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    private static FullTextResponse emptyResponse() {
        return new FullTextResponse(
                new FullTextResponse.ScopeInfo("rel-1", "build-1", 3), List.of());
    }

    @Test
    @DisplayName("X-KB-User binds through to the service")
    void identityHeaderBinds() throws Exception {
        when(fullTextService.fetch(any(), any())).thenReturn(emptyResponse());

        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .header("X-KB-User", "alice")
                        .content("{\"refs\":[{\"type\":\"raw_segment\",\"id\":\"seg-1\"}]}"))
                .andExpect(status().isOk());

        verify(fullTextService).fetch(any(), eq("alice"));
    }

    @Test
    @DisplayName("no identity header means anonymous, not a rejection")
    void anonymousIsAllowed() throws Exception {
        when(fullTextService.fetch(any(), any())).thenReturn(emptyResponse());

        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\":[{\"type\":\"raw_segment\",\"id\":\"seg-1\"}]}"))
                .andExpect(status().isOk());

        verify(fullTextService).fetch(any(), isNull());
    }

    @Test
    @DisplayName("snake_case aliases bind: kb_ids, paradigm_id, paradigm_version")
    void snakeCaseAliasesBind() throws Exception {
        when(fullTextService.fetch(any(), any())).thenReturn(emptyResponse());

        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"refs":[{"type":"retrieval_unit","id":"ru-1"}],
                                 "domain":"cloud_core_network",
                                 "kb_ids":["kb-a"],
                                 "paradigm_version":3}"""))
                .andExpect(status().isOk());

        ArgumentCaptor<FullTextRequest> captor = ArgumentCaptor.forClass(FullTextRequest.class);
        verify(fullTextService).fetch(captor.capture(), any());
        assertThat(captor.getValue().kbIds()).containsExactly("kb-a");
        assertThat(captor.getValue().paradigmVersion()).isEqualTo(3);
        assertThat(captor.getValue().refs()).hasSize(1);
    }

    @Test
    @DisplayName("response keeps found/reason so a caller can tell a miss from empty text")
    void missIsVisibleInJson() throws Exception {
        FullTextRequest.Ref ref =
                new FullTextRequest.Ref(FullTextRequest.TYPE_RAW_SEGMENT, "seg-gone");
        when(fullTextService.fetch(any(), any())).thenReturn(new FullTextResponse(
                new FullTextResponse.ScopeInfo("kb:kb-a", null, 2),
                List.of(FullTextResponse.Item.miss(ref))));

        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\":[{\"type\":\"raw_segment\",\"id\":\"seg-gone\"}]}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].found").value(false))
                .andExpect(jsonPath("$.items[0].reason").value("out_of_scope"))
                .andExpect(jsonPath("$.scope.releaseId").value("kb:kb-a"))
                .andExpect(jsonPath("$.scope.buildId").doesNotExist());
    }

    // ---------------------------------------------------------------- errors

    @Test
    @DisplayName("kb_not_found surfaces as 404 without naming which id failed")
    void kbNotFoundIs404() throws Exception {
        when(fullTextService.fetch(any(), any()))
                .thenThrow(new IllegalArgumentException("kb_not_found"));

        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\":[{\"type\":\"raw_segment\",\"id\":\"seg-1\"}],"
                                + "\"kb_ids\":[\"kb-secret\"]}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("kb_not_found"))
                .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.not(
                        org.hamcrest.Matchers.containsString("kb-secret"))));
    }

    @Test
    @DisplayName("conflicting_scope_source and too_many_refs surface as 400 with their own codes")
    void badRequestCodesAreMapped() throws Exception {
        when(fullTextService.fetch(any(), any()))
                .thenThrow(new IllegalArgumentException("conflicting_scope_source"));

        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\":[{\"type\":\"raw_segment\",\"id\":\"seg-1\"}]}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("conflicting_scope_source"));

        reset(fullTextService);
        when(fullTextService.fetch(any(), any()))
                .thenThrow(new IllegalArgumentException("too_many_refs"));

        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\":[{\"type\":\"raw_segment\",\"id\":\"seg-1\"}]}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("too_many_refs"));
    }

    @Test
    @DisplayName("empty_scope is its own code, not a generic bad_request")
    void emptyScopeIsMapped() throws Exception {
        when(fullTextService.fetch(any(), any()))
                .thenThrow(new IllegalArgumentException("empty_scope"));

        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\":[{\"type\":\"raw_segment\",\"id\":\"seg-1\"}]}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("empty_scope"));
    }
}
