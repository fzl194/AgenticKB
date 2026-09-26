package com.coremasterkb.serving.api;

import com.coremasterkb.serving.domainpack.DomainRegistry;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Read-only admin diagnostics. Configuration changes take effect through the
 * supervised backend restart workflow.
 */
@RestController
@RequestMapping("/api/v1/admin")
public class AdminController {

    private final DomainRegistry domainRegistry;

    public AdminController(DomainRegistry domainRegistry) {
        this.domainRegistry = domainRegistry;
    }

    @GetMapping("/config-status")
    public ResponseEntity<Map<String, Object>> configStatus() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("loaded", domainRegistry.isLoaded());
        body.put("domains", domainRegistry.knownDomains());
        return ResponseEntity.ok(body);
    }
}
