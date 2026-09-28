package com.coremasterkb.serving.mapper;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

class KnowledgeAccessRecordMapperXmlTest {
    @Test
    void mapperFinalizesOnlyPendingRowsWithoutOverwritingMcpAttribution() throws Exception {
        try (var stream = getClass().getClassLoader()
                .getResourceAsStream("mapper/KnowledgeAccessRecordMapper.xml")) {
            assertThat(stream).isNotNull();
            String xml = new String(stream.readAllBytes(), StandardCharsets.UTF_8);
            assertThat(xml).contains("INSERT INTO knowledge_access_records");
            assertThat(xml).contains("ON CONFLICT (id) DO UPDATE SET");
            assertThat(xml).contains("WHERE knowledge_access_records.status = 'pending'");
            assertThat(xml).contains("CAST(#{kbIdsJson} AS jsonb)");
            assertThat(xml).contains("COALESCE(#{actorUserId},");
            assertThat(xml).contains("SELECT id FROM kb_users WHERE username");
            assertThat(xml).doesNotContain("serving_query_logs");

            String update = xml.substring(xml.indexOf("ON CONFLICT (id) DO UPDATE SET"));
            assertThat(update).contains(
                    "completed_at = EXCLUDED.completed_at",
                    "status = EXCLUDED.status",
                    "result_count = EXCLUDED.result_count",
                    "duration_ms = EXCLUDED.duration_ms",
                    "error_code = EXCLUDED.error_code",
                    "details_json = knowledge_access_records.details_json || EXCLUDED.details_json");
            assertThat(update).doesNotContain(
                    "actor_user_id =",
                    "actor_username =",
                    "source =",
                    "operation =",
                    "tool_name =",
                    "mcp_key_id =",
                    "kb_ids =",
                    "query_text =");

            assertThat(xml).contains(
                    "<insert id=\"upsertPayload\"",
                    "INSERT INTO knowledge_access_record_payloads",
                    "ON CONFLICT (record_id) DO UPDATE SET",
                    "effective_context_json = EXCLUDED.effective_context_json",
                    "response_json = COALESCE(EXCLUDED.response_json");
            String payloadUpdate = xml.substring(
                    xml.indexOf("ON CONFLICT (record_id) DO UPDATE SET"));
            assertThat(payloadUpdate).doesNotContain(
                    "request_json =",
                    "request_bytes =");
        }
    }
}
