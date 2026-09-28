package com.coremasterkb.serving.observability;

import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.entity.KnowledgeAccessRecord;
import com.coremasterkb.serving.entity.KnowledgeAccessRecordPayload;
import com.coremasterkb.serving.mapper.KnowledgeAccessRecordMapper;
import com.coremasterkb.serving.operator.api.ParadigmExecutionService.RunArgs;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeoutException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class KnowledgeAccessRecordServiceTest {

    private KnowledgeAccessRecordMapper mapper;
    private KnowledgeAccessRecordService service;

    @BeforeEach
    void setUp() {
        mapper = mock(KnowledgeAccessRecordMapper.class);
        when(mapper.insert(any())).thenReturn(1);
        service = new KnowledgeAccessRecordService(mapper);
    }

    @Test
    void recordsWebSearchIdentityScopeAndCompletePayload() {
        RunArgs args = args("call-1", "web", List.of("kb-1"), "alice")
                .withParadigm("pd-1", 4)
                .withIdentity("user-1")
                .withRequestJson("{\"query\":\"full question\",\"top_k\":12}");
        EvidenceResponse.EvidenceItem item = new EvidenceResponse.EvidenceItem(
                "ev_1", "prose", "secret evidence body",
                new EvidenceResponse.EvidenceSource(
                        "kb", "manual.md", null, "doc_1", null, null, null),
                false, null);

        service.record(
                Instant.parse("2026-09-28T10:00:00Z"), args,
                Map.of("evidenceResponse", new EvidenceResponse("q", List.of(item), false)),
                null, 23);

        KnowledgeAccessRecord record = captured();
        assertThat(record.getId()).isEqualTo("call-1");
        assertThat(record.getSource()).isEqualTo("web");
        assertThat(record.getOperation()).isEqualTo("search");
        assertThat(record.getActorUsername()).isEqualTo("alice");
        assertThat(record.getActorUserId()).isEqualTo("user-1");
        assertThat(record.getKbIdsJson()).isEqualTo("[\"kb-1\"]");
        assertThat(record.getParadigmId()).isEqualTo("pd-1");
        assertThat(record.getParadigmVersion()).isEqualTo(4);
        assertThat(record.getStatus()).isEqualTo("success");
        assertThat(record.getResultCount()).isEqualTo(1);
        assertThat(record.getDurationMs()).isEqualTo(23);
        assertThat(record.getDetailsJson()).doesNotContain("secret evidence body");

        KnowledgeAccessRecordPayload payload = capturedPayload();
        assertThat(payload.getRecordId()).isEqualTo("call-1");
        assertThat(payload.getRequestJson()).contains("full question", "\"top_k\":12");
        assertThat(payload.getEffectiveContextJson())
                .contains("\"kb_ids\":[\"kb-1\"]", "\"paradigm_id\":\"pd-1\"");
        assertThat(payload.getResponseMode()).isEqualTo("snapshot");
        assertThat(payload.getResponseJson()).contains("secret evidence body", "\"ref\":\"ev_1\"");
        assertThat(payload.getResponseRefsJson()).contains("ev_1", "doc_1");
        assertThat(payload.isResponseTruncated()).isFalse();
    }

    @Test
    void recordsNoResultSeparatelyFromFailures() {
        service.record(Instant.now(), args("call-2", "api", List.of("kb-2"), "bob"),
                Map.of("evidenceResponse", new EvidenceResponse("q", List.of(), false)),
                null, 9);
        KnowledgeAccessRecord record = captured();
        assertThat(record.getStatus()).isEqualTo("no_result");
        assertThat(record.getResultCount()).isZero();
        assertThat(record.getErrorCode()).isNull();
    }

    @Test
    void classifiesOnlyExplicitTimeoutTypes() {
        assertFailure(new IllegalArgumentException("query_required"), "invalid", "invalid_request");
        assertFailure(new TimeoutException("backend details"), "timeout", "execution_timeout");
        assertFailure(new RuntimeException(new java.net.SocketTimeoutException("socket")),
                "timeout", "execution_timeout");
        assertFailure(new FakeTimeoutException("only the class name says timeout"),
                "failed", "execution_failed");
        assertFailure(new IllegalStateException("db password=secret"), "failed", "execution_failed");
    }

    @Test
    void mcpSearchStillAttemptsInsertSoSpoofedSourceCannotEvadeRecording() {
        service.record(Instant.now(), args("mcp-1", "mcp", List.of("kb-1"), "alice"),
                Map.of("evidenceResponse", new EvidenceResponse("q", List.of(), false)),
                null, 7);
        KnowledgeAccessRecord record = captured();
        assertThat(record.getStatus()).isEqualTo("no_result");
        assertThat(record.getDetailsJson()).contains("\"terminal_owner\":\"serving\"");
    }

    @Test
    void candidateOnlyEmptyResultIsNoResult() {
        service.record(Instant.now(), args("call-c", "api", List.of(), "alice"),
                Map.of("candidates", List.of()), null, 4);
        KnowledgeAccessRecord record = captured();
        assertThat(record.getStatus()).isEqualTo("no_result");
        assertThat(record.getResultCount()).isZero();
    }

    @Test
    void mapperFailureIsCountedWithoutChangingBusinessOutcome() {
        doThrow(new RuntimeException("db down")).when(mapper).insert(any());
        service.record(Instant.now(), args("call-3", "api", List.of(), "alice"),
                Map.of(), null, 1);
        assertThat(service.writeFailures()).isEqualTo(1);
    }

    @Test
    void payloadFailureIsCountedWithoutChangingBusinessOutcome() {
        doThrow(new RuntimeException("payload db down")).when(mapper).upsertPayload(any());
        service.record(Instant.now(), args("call-p", "api", List.of(), "alice"),
                Map.of("candidates", List.of()), null, 1);
        assertThat(service.writeFailures()).isEqualTo(1);
        verify(mapper).insert(any());
    }

    @Test
    void terminalReplayCannotOverwriteExistingPayload() {
        when(mapper.insert(any())).thenReturn(0);
        service.record(Instant.now(), args("same-id", "api", List.of(), "alice"),
                Map.of("candidates", List.of(Map.of("content", "late replay"))), null, 1);
        verify(mapper, never()).upsertPayload(any());
        assertThat(service.writeFailures()).isZero();
    }

    @Test
    void recursivelyRedactsSensitiveBusinessKeysButPreservesQueryAndEvidenceContent() {
        RunArgs args = args("safe-1", "api", List.of("kb-1"), "alice")
                .withRequestJson("{\"query\":\"正文 token=literal\",\"filters\":{\"password\":\"p\","
                        + "\"nested\":{\"api_key\":\"k\",\"allowed\":\"yes\"}}}");
        Map<String, Object> result = Map.of("evidence", List.of(Map.of(
                "content", "证据正文 password=literal", "authorization", "Bearer secret",
                "source", Map.of("cookie", "secret", "document_ref", "doc_1"))));
        service.record(Instant.now(), args, result, null, 1);
        KnowledgeAccessRecordPayload payload = capturedPayload();
        assertThat(payload.getRequestJson()).contains("正文 token=literal", "allowed", "yes")
                .doesNotContain("password\"", "api_key", "\"p\"", "\"k\"");
        assertThat(payload.getResponseJson())
                .contains("证据正文 password=literal", "document_ref", "doc_1")
                .doesNotContain("authorization", "Bearer secret", "cookie");
        assertThat(payload.getRedactionsJson())
                .contains("filters.password", "filters.nested.api_key", "evidence[0].authorization");
    }

    @Test
    void boundsRequestAndResponseWithoutCuttingACollectionItem() {
        String huge = "测".repeat(300_000);
        RunArgs args = args("bounded-1", "api", List.of(), "alice")
                .withRequestJson("{\"query\":\"" + huge + "\"}");
        List<Map<String, Object>> evidence = List.of(
                Map.of("ref", "ev_1", "content", "ok"),
                Map.of("ref", "ev_2", "content", "x".repeat(4 * 1024 * 1024)));
        service.record(Instant.now(), args, Map.of("evidence", evidence), null, 1);
        KnowledgeAccessRecordPayload payload = capturedPayload();
        assertThat(payload.getRequestBytes()).isLessThanOrEqualTo(256 * 1024);
        assertThat(payload.getRequestJson()).contains("__truncated__");
        assertThat(payload.getResponseBytes()).isLessThanOrEqualTo(4 * 1024 * 1024);
        assertThat(payload.isResponseTruncated()).isTrue();
        assertThat(payload.getResponseOmittedCount()).isEqualTo(1);
        assertThat(payload.getResponseJson()).contains("ev_1").doesNotContain("ev_2");
        assertThat(payload.getResponseOriginalBytes()).isGreaterThan(payload.getResponseBytes());
        assertThat(payload.getResponseSha256()).hasSize(64);
    }

    @Test
    void failurePayloadContainsOnlyPublicCodeAndMessage() {
        service.record(Instant.now(), args("failed-1", "api", List.of(), "alice"), null,
                new IllegalStateException("password=db-secret stack=/internal/path"), 1);
        KnowledgeAccessRecordPayload payload = capturedPayload();
        assertThat(payload.getResponseMode()).isEqualTo("summary");
        assertThat(payload.getResponseJson()).contains("execution_failed", "检索执行失败")
                .doesNotContain("db-secret", "internal/path", "stack");
    }
    @Test
    void queryTextIsOnlyAUnicodeSafeFourThousandCodePointPreview() {
        String query = "🙂".repeat(4_001);
        service.record(Instant.now(),
                new RunArgs(query, "d", "prod", false, "alice")
                        .withAccess("preview", "api"), Map.of(), null, 1);
        KnowledgeAccessRecord record = captured();
        assertThat(record.getQueryText().codePointCount(0, record.getQueryText().length()))
                .isEqualTo(4_000);
    }

    @Test
    void missingTrustedIdentityOrAccessIdMarksAttributionDegraded() {
        service.record(Instant.now(), new RunArgs("q", "d", "prod", false, "legacy"),
                Map.of(), null, 1);
        assertThat(captured().getDetailsJson()).contains("\"attribution_degraded\":true");
    }

    private void assertFailure(Throwable failure, String status, String code) {
        reset(mapper);
        service.record(Instant.now(), args("call-f", "api", List.of(), "alice"),
                null, failure, 11);
        KnowledgeAccessRecord record = captured();
        assertThat(record.getStatus()).isEqualTo(status);
        assertThat(record.getErrorCode()).isEqualTo(code);
        assertThat(record.getDetailsJson()).doesNotContain(failure.getMessage());
    }

    private KnowledgeAccessRecord captured() {
        ArgumentCaptor<KnowledgeAccessRecord> captor =
                ArgumentCaptor.forClass(KnowledgeAccessRecord.class);
        verify(mapper).insert(captor.capture());
        return captor.getValue();
    }

    private KnowledgeAccessRecordPayload capturedPayload() {
        ArgumentCaptor<KnowledgeAccessRecordPayload> captor =
                ArgumentCaptor.forClass(KnowledgeAccessRecordPayload.class);
        verify(mapper).upsertPayload(captor.capture());
        return captor.getValue();
    }

    private static RunArgs args(String id, String source, List<String> kbIds, String username) {
        return new RunArgs("q", "cloud_core_network", "prod", false, username)
                .withKbIds(kbIds)
                .withAccess(id, source);
    }

    private static final class FakeTimeoutException extends RuntimeException {
        private FakeTimeoutException(String message) { super(message); }
    }
}
