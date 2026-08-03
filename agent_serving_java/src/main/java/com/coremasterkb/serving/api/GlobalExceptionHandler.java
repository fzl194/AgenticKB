package com.coremasterkb.serving.api;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import java.util.Map;

@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Map<String, Object>> handleIllegalArgument(IllegalArgumentException ex) {
        if ("query_required".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "query_required", "message", "Query text is required and must not be blank"));
        }
        if ("unknown_domain".equals(ex.getMessage())) {
            log.warn("Unknown domain in request");
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "unknown_domain", "message", "Unknown or unsupported domain"));
        }
        if ("domain_disabled".equals(ex.getMessage())) {
            log.warn("Disabled domain in request");
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "domain_disabled", "message", "This domain is currently disabled"));
        }
        if ("no_active_release".equals(ex.getMessage())) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Map.of("error", "no_active_release", "message", "No active release found for the requested domain"));
        }
        if ("multiple_active_releases".equals(ex.getMessage())) {
            return ResponseEntity.status(HttpStatus.CONFLICT)
                    .body(Map.of("error", "multiple_active_releases", "message", "Multiple active releases found"));
        }
        // Forbidden and nonexistent knowledge bases deliberately share this response, so a caller
        // cannot probe for which ids exist.
        if ("kb_not_found".equals(ex.getMessage())) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                    .body(Map.of("error", "kb_not_found",
                            "message", "One or more knowledge bases were not found"));
        }
        if ("kb_ids_required".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "kb_ids_required", "message", "At least one knowledge base id is required"));
        }
        // Mapped explicitly so an un-mined KB reports itself instead of collapsing into the
        // generic bad_request below — "empty results" and "nothing mined yet" look identical
        // to a caller otherwise.
        if ("no_active_kb_build".equals(ex.getMessage())) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                    .body(Map.of("error", "no_active_kb_build",
                            "message", "The selected knowledge bases have no mined content"));
        }
        log.warn("Bad request: {}", ex.getMessage());
        return ResponseEntity.badRequest()
                .body(Map.of("error", "bad_request", "message", "Bad request"));
    }

    @ExceptionHandler(IllegalStateException.class)
    public ResponseEntity<Map<String, Object>> handleIllegalState(IllegalStateException ex) {
        if ("scenario_pack_missing".equals(ex.getMessage())) {
            log.error("Scenario pack missing — deployment configuration error");
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("error", "scenario_pack_missing",
                            "message", "Scenario pack not found — deployment configuration error"));
        }
        if ("domain_database_unavailable".equals(ex.getMessage())) {
            log.error("Domain database unavailable");
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Map.of("error", "domain_database_unavailable",
                            "message", "Domain database is currently unavailable"));
        }
        log.error("Unexpected state: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(Map.of("error", "internal_error", "message", "Internal server error"));
    }

    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<Map<String, Object>> handleNoResourceFound(NoResourceFoundException ex) {
        // 扫描器/爬虫探测不存在的路径是常态，降级为单行 WARN + 404，不打印堆栈，避免日志刷屏
        log.warn("Resource not found: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Map.of("error", "not_found", "message", "Resource not found"));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, Object>> handleGeneric(Exception ex) {
        log.error("Unhandled exception", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(Map.of("error", "internal_error", "message", "Internal server error"));
    }
}
