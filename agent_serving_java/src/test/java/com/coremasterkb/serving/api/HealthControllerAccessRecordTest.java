package com.coremasterkb.serving.api;

import com.coremasterkb.serving.observability.KnowledgeAccessRecordService;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class HealthControllerAccessRecordTest {
    @Test
    void exposesAccessRecordWriteFailures() {
        KnowledgeAccessRecordService records = mock(KnowledgeAccessRecordService.class);
        when(records.writeFailures()).thenReturn(3L);
        HealthController controller = new HealthController(records);
        var response = controller.health();
        assertThat(response.getStatusCode().is2xxSuccessful()).isTrue();
        assertThat(response.getBody())
                .containsEntry("status", "ok")
                .containsEntry("access_record_write_failures", 3L);
    }
}
