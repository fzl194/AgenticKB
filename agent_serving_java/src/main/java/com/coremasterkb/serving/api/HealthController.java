package com.coremasterkb.serving.api;

import com.coremasterkb.serving.observability.KnowledgeAccessRecordService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
public class HealthController {
    private final KnowledgeAccessRecordService accessRecords;

    public HealthController(KnowledgeAccessRecordService accessRecords) {
        this.accessRecords = accessRecords;
    }

    @GetMapping("/actuator/health")
    public ResponseEntity<Map<String, Object>> health() {
        return ResponseEntity.ok(Map.of(
                "status", "ok",
                "version", "0.1.0",
                "access_record_write_failures", accessRecords.writeFailures()));
    }
}
