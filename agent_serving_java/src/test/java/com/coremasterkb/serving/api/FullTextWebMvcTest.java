package com.coremasterkb.serving.api;

import com.coremasterkb.serving.application.FullTextService;
import com.coremasterkb.serving.application.RawFileService;
import com.coremasterkb.serving.domain.FullTextRequest;
import com.coremasterkb.serving.domain.FullTextResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
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
    private RawFileService rawFileService;

    @BeforeEach
    void setUp() {
        fullTextService = mock(FullTextService.class);
        rawFileService = mock(RawFileService.class);
        mockMvc = MockMvcBuilders
                .standaloneSetup(new FullTextController(fullTextService, rawFileService))
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

    // --------------------------------------- validation inside the request record
    //
    // These go through real deserialization rather than a mocked throw. The record's compact
    // constructor runs inside Jackson, so its IllegalArgumentException arrives wrapped in
    // HttpMessageNotReadableException — the mapped codes below are unreachable unless the advice
    // unwraps it, and a mocked-service test cannot tell the difference.

    @Test
    @DisplayName("an unknown granularity is a 400 with its own code, not a 500")
    void unknownGranularityIsMapped() throws Exception {
        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\":[{\"type\":\"raw_segment\",\"id\":\"s1\"}],"
                                + "\"granularity\":\"paragraph\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("unknown_granularity"));

        verifyNoInteractions(fullTextService);
    }

    @Test
    @DisplayName("an out-of-range window radius is a 400 with its own code")
    void windowRadiusOutOfRangeIsMapped() throws Exception {
        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\":[{\"type\":\"raw_segment\",\"id\":\"s1\"}],"
                                + "\"granularity\":\"window\",\"windowRadius\":99}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("window_radius_out_of_range"));
    }

    @Test
    @DisplayName("an unknown ref type is a 400 with its own code")
    void unknownRefTypeIsMapped() throws Exception {
        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\":[{\"type\":\"nonsense\",\"id\":\"s1\"}]}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("unknown_ref_type"));
    }

    @Test
    @DisplayName("a blank ref id is a 400 with its own code")
    void blankRefIdIsMapped() throws Exception {
        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\":[{\"type\":\"raw_segment\",\"id\":\"  \"}]}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("ref_id_required"));
    }

    @Test
    @DisplayName("unparseable JSON is a 400 malformed_request, not a server error")
    void malformedJsonIsClientError() throws Exception {
        mockMvc.perform(post("/api/v1/segments/fulltext")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refs\": [ truncated"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("malformed_request"));
    }

    // ------------------------------------------------------- raw file endpoint

    @Test
    @DisplayName("raw streams the file with an RFC 5987 filename so Chinese names survive")
    void rawStreamsFile(@TempDir Path tmp) throws Exception {
        Path file = Files.writeString(tmp.resolve("f.pdf"), "PDF-BYTES");
        when(rawFileService.resolve(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new RawFileService.RawFile(
                        file, "核心网规范.pdf", "application/pdf", Files.size(file)));

        var result = mockMvc.perform(get("/api/v1/documents/doc-1/raw")
                        .param("domain", "cloud_core_network")
                        .header("X-KB-User", "alice"))
                .andExpect(status().isOk())
                .andExpect(content().contentType("application/pdf"))
                .andReturn();

        String disposition = result.getResponse().getHeader("Content-Disposition");
        assertThat(disposition).contains("filename*=UTF-8''");
        // The raw name must not appear unencoded — that is the form that arrives mojibake.
        assertThat(disposition).doesNotContain("核心网规范.pdf");
        assertThat(result.getResponse().getContentAsString()).isEqualTo("PDF-BYTES");
    }

    @Test
    @DisplayName("raw passes scope parameters and identity through to the service")
    void rawBindsScopeParameters(@TempDir Path tmp) throws Exception {
        Path file = Files.writeString(tmp.resolve("f.pdf"), "x");
        when(rawFileService.resolve(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new RawFileService.RawFile(file, "f.pdf", "application/pdf", 1));

        mockMvc.perform(get("/api/v1/documents/doc-1/raw")
                        .param("domain", "cloud_core_network")
                        .param("channel", "staging")
                        .param("kbIds", "kb-a", "kb-b")
                        .header("X-KB-User", "alice"))
                .andExpect(status().isOk());

        verify(rawFileService).resolve("doc-1", "cloud_core_network", "staging",
                null, null, List.of("kb-a", "kb-b"), "alice");
    }

    @Test
    @DisplayName("a document with no original file is 404 raw_file_unavailable, not document_not_found")
    void rawFileUnavailableIsDistinct() throws Exception {
        when(rawFileService.resolve(any(), any(), any(), any(), any(), any(), any()))
                .thenThrow(new IllegalArgumentException("raw_file_unavailable"));

        mockMvc.perform(get("/api/v1/documents/doc-legacy/raw"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("raw_file_unavailable"));
    }

    @Test
    @DisplayName("an out-of-scope document is 404 document_not_found")
    void rawDocumentNotFound() throws Exception {
        when(rawFileService.resolve(any(), any(), any(), any(), any(), any(), any()))
                .thenThrow(new IllegalArgumentException("document_not_found"));

        mockMvc.perform(get("/api/v1/documents/doc-other/raw"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("document_not_found"));
    }

    @Test
    @DisplayName("a misconfigured upload root is 503, telling ops apart from a missing file")
    void rawStorageUnavailableIs503() throws Exception {
        when(rawFileService.resolve(any(), any(), any(), any(), any(), any(), any()))
                .thenThrow(new IllegalStateException("raw_file_storage_unavailable"));

        mockMvc.perform(get("/api/v1/documents/doc-1/raw"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("raw_file_storage_unavailable"));
    }
}
