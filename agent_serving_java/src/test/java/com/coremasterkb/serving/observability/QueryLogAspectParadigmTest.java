package com.coremasterkb.serving.observability;

import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.domain.SearchRequest;
import com.coremasterkb.serving.operator.api.ParadigmExecutionService.RunArgs;
import org.aspectj.lang.ProceedingJoinPoint;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

/**
 * The paradigm engine's query-log advice.
 *
 * <p>Its whole reason for existing is that {@code serving_query_logs} had no coverage of the
 * paradigm path — so the assertions here are mostly "a row is still written", including on the
 * paths where it is tempting to skip: an execution that threw, and one that produced candidates
 * rather than an EvidenceResponse.</p>
 */
@DisplayName("QueryLogAspect — paradigm path")
class QueryLogAspectParadigmTest {

    private QueryLogService queryLogService;
    private QueryLogAspect aspect;

    @BeforeEach
    void setUp() {
        queryLogService = mock(QueryLogService.class);
        aspect = new QueryLogAspect(queryLogService);
    }

    @Test
    @DisplayName("attributes the row to the paradigm and its effective version")
    void recordsParadigmAttribution() throws Throwable {
        EvidenceResponse response = emptyResponse();
        ProceedingJoinPoint pjp = jp(args("SMF 配置", "odn", "pd-abc", 3),
                Map.of("evidenceResponse", response));

        aspect.logParadigmSearch(pjp);

        Map<String, Object> meta = capturedMetadata();
        assertEquals("paradigm", meta.get("engine"));
        assertEquals("pd-abc", meta.get("paradigm_id"));
        assertEquals(3, meta.get("paradigm_version"));
        assertEquals("evidenceResponse", meta.get("output"));
        assertFalse((Boolean) meta.get("has_more"));

        assertSame(response, capturedResponse(), "the EvidenceResponse must be unwrapped from the shaped map");
        SearchRequest req = capturedRequest();
        assertEquals("SMF 配置", req.query());
        assertEquals("odn", req.domain());
    }

    @Test
    @DisplayName("has_more=true is carried into the log metadata")
    void carriesHasMore() throws Throwable {
        EvidenceResponse response = new EvidenceResponse("q", List.of(), true);
        ProceedingJoinPoint pjp = jp(args("q", "odn", "pd-abc", 3),
                Map.of("evidenceResponse", response));

        aspect.logParadigmSearch(pjp);

        assertEquals(true, capturedMetadata().get("has_more"));
    }

    @Test
    @DisplayName("a candidate-only run is still logged, with no response")
    void recordsCandidateOnlyRun() throws Throwable {
        ProceedingJoinPoint pjp = jp(args("q", "odn", "pd-eval", 1),
                Map.of("candidates", List.of(Map.of("id", "ru-1"))));

        aspect.logParadigmSearch(pjp);

        assertNull(capturedResponse());
        assertEquals("candidates", capturedMetadata().get("output"));
    }

    @Test
    @DisplayName("a failed execution is logged and the exception still propagates")
    void recordsFailureAndRethrows() throws Throwable {
        ProceedingJoinPoint pjp = mock(ProceedingJoinPoint.class);
        when(pjp.getArgs()).thenReturn(new Object[]{null, args("q", "odn", "pd-x", 2)});
        when(pjp.proceed()).thenThrow(new IllegalStateException("boom"));

        var ex = assertThrows(IllegalStateException.class, () -> aspect.logParadigmSearch(pjp));
        assertEquals("boom", ex.getMessage());

        assertNull(capturedResponse());
        assertEquals("none", capturedMetadata().get("output"));
    }

    @Test
    @DisplayName("inline runs carry no paradigm id, only the engine tag")
    void inlineRunHasNoParadigmId() throws Throwable {
        RunArgs inline = new RunArgs("q", "odn", "prod", false, null);
        ProceedingJoinPoint pjp = jp(inline, Map.of("evidenceResponse", emptyResponse()));

        aspect.logParadigmSearch(pjp);

        Map<String, Object> meta = capturedMetadata();
        assertEquals("paradigm", meta.get("engine"));
        assertFalse(meta.containsKey("paradigm_id"));
        assertFalse(meta.containsKey("paradigm_version"));
    }

    /**
     * A blank query makes {@code SearchRequest}'s compact constructor throw. That must stay a 400
     * from the execution service, never become a 500 raised out of the logging advice.
     */
    @Test
    @DisplayName("a query the log cannot represent does not break the request")
    void blankQueryDoesNotEscapeAsAnError() throws Throwable {
        ProceedingJoinPoint pjp = jp(args("", "odn", "pd-abc", 1), Map.of());

        assertDoesNotThrow(() -> aspect.logParadigmSearch(pjp));
        verify(queryLogService, never()).record(anyString(), any(), any(), anyLong(), any());
    }

    @Test
    @DisplayName("an unexpected signature falls through instead of failing the call")
    void toleratesUnexpectedSignature() throws Throwable {
        ProceedingJoinPoint pjp = mock(ProceedingJoinPoint.class);
        when(pjp.getArgs()).thenReturn(new Object[]{"only-one-arg"});
        when(pjp.proceed()).thenReturn("passthrough");

        assertEquals("passthrough", aspect.logParadigmSearch(pjp));
        verifyNoInteractions(queryLogService);
    }

    // ---- helpers --------------------------------------------------------------------------

    private static RunArgs args(String query, String domain, String paradigmId, Integer version) {
        return new RunArgs(query, domain, "prod", false, "tester").withParadigm(paradigmId, version);
    }

    private static ProceedingJoinPoint jp(RunArgs runArgs, Object result) throws Throwable {
        ProceedingJoinPoint pjp = mock(ProceedingJoinPoint.class);
        when(pjp.getArgs()).thenReturn(new Object[]{null, runArgs});
        when(pjp.proceed()).thenReturn(result);
        return pjp;
    }

    private static EvidenceResponse emptyResponse() {
        return new EvidenceResponse("q", List.of(), false);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> capturedMetadata() {
        ArgumentCaptor<Map<String, Object>> cap = ArgumentCaptor.forClass(Map.class);
        verify(queryLogService).record(anyString(), any(), any(), anyLong(), cap.capture());
        return cap.getValue();
    }

    private EvidenceResponse capturedResponse() {
        ArgumentCaptor<EvidenceResponse> cap = ArgumentCaptor.forClass(EvidenceResponse.class);
        verify(queryLogService).record(anyString(), any(), cap.capture(), anyLong(), any());
        return cap.getValue();
    }

    private SearchRequest capturedRequest() {
        ArgumentCaptor<SearchRequest> cap = ArgumentCaptor.forClass(SearchRequest.class);
        verify(queryLogService).record(anyString(), cap.capture(), any(), anyLong(), any());
        return cap.getValue();
    }
}
