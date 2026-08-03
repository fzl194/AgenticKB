package com.coremasterkb.serving.operator.api;

import com.coremasterkb.serving.operator.engine.ParadigmCompiler;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Inline (non-persisted) paradigm execution and validation. Lets the test system / a developer
 * POST a paradigm graph directly without first storing it. Persisted paradigms (stored, versioned,
 * called by id) live in {@link ParadigmController}.
 *
 * <p>Runs alongside the existing {@code /api/v1/search}; that pipeline is untouched.</p>
 */
@RestController
@RequestMapping("/api/v1/paradigm")
public class ParadigmRunController {

    private final ParadigmCompiler compiler;
    private final ParadigmExecutionService executionService;

    public ParadigmRunController(ParadigmCompiler compiler, ParadigmExecutionService executionService) {
        this.compiler = compiler;
        this.executionService = executionService;
    }

    /** Compile-only: validate an inline paradigm graph, returning structured errors (no execution). */
    @PostMapping("/validate")
    public Map<String, Object> validate(@RequestBody JsonNode body) {
        JsonNode paradigm = body.has("paradigm") ? body.get("paradigm") : body;
        var errors = compiler.validate(paradigm);
        Map<String, Object> resp = new LinkedHashMap<>();
        resp.put("valid", errors.isEmpty());
        resp.put("errors", errors);
        return resp;
    }

    /**
     * Compile + execute an inline paradigm.
     * Body: {@code { "paradigm": {nodes,edges,output}, "query": "...", "domain"?, "channel"?, "debug"? }}.
     */
    @PostMapping("/run")
    public ResponseEntity<Map<String, Object>> run(
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        JsonNode paradigm = body.get("paradigm");
        if (paradigm == null || paradigm.isNull()) {
            return ResponseEntity.badRequest().body(Map.of("error", "missing 'paradigm'"));
        }
        var args = ParadigmRequests.toRunArgs(body, kbUser);
        return ResponseEntity.ok(executionService.run(paradigm, args));
    }
}
