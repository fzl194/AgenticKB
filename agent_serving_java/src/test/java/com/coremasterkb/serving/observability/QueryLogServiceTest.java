package com.coremasterkb.serving.observability;

import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.domain.SearchRequest;
import com.coremasterkb.serving.entity.ServingQueryLog;
import com.coremasterkb.serving.mapper.ServingQueryLogMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@DisplayName("QueryLogService")
class QueryLogServiceTest {

    private ServingQueryLogMapper logMapper;
    private QueryLogService service;

    @BeforeEach
    void setUp() {
        logMapper = mock(ServingQueryLogMapper.class);
        service = new QueryLogService(logMapper);
    }

    private SearchRequest req(String query, String domain, String channel) {
        return new SearchRequest(query, Map.of(), List.of(), false, domain, channel, "evidence");
    }

    private EvidenceResponse.EvidenceItem item(String ref, String type, String fileName) {
        return new EvidenceResponse.EvidenceItem(ref, type, "content", new EvidenceResponse.EvidenceSource(
                "kb", fileName, null, "doc_x", null, null), false, null);
    }

    @Nested
    @DisplayName("channel resolution")
    class ChannelResolution {
        @Test
        @DisplayName("uses explicit channel when provided")
        void usesExplicitChannel() {
            service.record("id1", req("q", "domain1", "beta"), null, 100);
            var captor = ArgumentCaptor.forClass(ServingQueryLog.class);
            verify(logMapper).insert(captor.capture());
            assertThat(captor.getValue().getChannel()).isEqualTo("beta");
        }

        @Test
        @DisplayName("falls back to 'prod' when channel is blank")
        void fallsBackToProd() {
            service.record("id2", req("q", "cloud_core_network", null), null, 100);
            var captor = ArgumentCaptor.forClass(ServingQueryLog.class);
            verify(logMapper).insert(captor.capture());
            assertThat(captor.getValue().getChannel()).isEqualTo("prod");
        }

        @Test
        @DisplayName("domain is stored separately from channel")
        void domainStoredSeparately() {
            service.record("id3", req("q", "cloud_core_network", null), null, 100);
            var captor = ArgumentCaptor.forClass(ServingQueryLog.class);
            verify(logMapper).insert(captor.capture());
            assertThat(captor.getValue().getDomain()).isEqualTo("cloud_core_network");
            assertThat(captor.getValue().getChannel()).isEqualTo("prod");
        }
    }

    @Nested
    @DisplayName("result fields from EvidenceResponse")
    class ResultFields {
        @Test
        @DisplayName("evidence count, intent and hasResult computed from the response")
        void evidenceCountAndHasResult() {
            var response = new EvidenceResponse("q",
                    List.of(item("ev_a", "prose", "a.md"), item("ev_b", "table_row", "b.xlsx")),
                    true);

            service.record("id4", req("q", "d", "prod"), response, 50);

            var captor = ArgumentCaptor.forClass(ServingQueryLog.class);
            verify(logMapper).insert(captor.capture());
            ServingQueryLog entry = captor.getValue();
            assertThat(entry.getResultItemCount()).isEqualTo(2);
            assertThat(entry.getResultSeedCount()).isEqualTo(2);
            assertThat(entry.getResultHasResult()).isTrue();
            assertThat(entry.getIntent()).isEqualTo("evidence");
            // 条目日志只含公开协议字段（ref/type/truncated/source），不落全文
            assertThat(entry.getResultItemsJson()).contains("ev_a").contains("table_row")
                    .doesNotContain("content");
        }

        @Test
        @DisplayName("distinct sources are logged once per document")
        void distinctSourcesLogged() throws Exception {
            var response = new EvidenceResponse("q",
                    List.of(item("ev_a", "prose", "a.md"), item("ev_b", "prose", "a.md")), false);

            service.record("id5", req("q", "d", "prod"), response, 20);

            var captor = ArgumentCaptor.forClass(ServingQueryLog.class);
            verify(logMapper).insert(captor.capture());
            String sourcesJson = captor.getValue().getResultSourcesJson();
            assertThat(sourcesJson).contains("a.md");
            // 同文档两条证据 → source 去重后只有一条
            int sourceCount = new com.fasterxml.jackson.databind.ObjectMapper()
                    .readValue(sourcesJson, List.class).size();
            assertThat(sourceCount).isEqualTo(1);
        }

        @Test
        @DisplayName("null response sets hasResult=false and skips result fields")
        void nullResponse() {
            service.record("id6", req("q", "d", "prod"), null, 10);

            var captor = ArgumentCaptor.forClass(ServingQueryLog.class);
            verify(logMapper).insert(captor.capture());
            assertThat(captor.getValue().getResultHasResult()).isFalse();
            // 历史语义：无结果时计数字段留空（与批次8 之前的空 pack 行一致）
            assertThat(captor.getValue().getResultItemCount()).isNull();
        }
    }

    @Nested
    @DisplayName("failure isolation")
    class FailureIsolation {
        @Test
        @DisplayName("mapper failure is swallowed — no exception propagates")
        void mapperFailureSwallowed() {
            doThrow(new RuntimeException("db down")).when(logMapper).insert(any());
            // should not throw
            service.record("id7", req("q", "d", "prod"), null, 10);
        }
    }
}
