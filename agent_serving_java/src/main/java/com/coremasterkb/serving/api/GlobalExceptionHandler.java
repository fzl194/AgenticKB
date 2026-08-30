package com.coremasterkb.serving.api;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
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
        // ---- full-text drill-down ----
        if ("conflicting_scope_source".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "conflicting_scope_source",
                            "message", "Supply either paradigmId or kbIds, not both"));
        }
        if ("too_many_refs".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "too_many_refs",
                            "message", "Too many refs in one request (max "
                                    + com.coremasterkb.serving.application.FullTextService.MAX_REFS + ")"));
        }
        if ("refs_required".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "refs_required", "message", "At least one ref is required"));
        }
        if ("unknown_ref_type".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "unknown_ref_type",
                            "message", "Ref type must be 'retrieval_unit' or 'raw_segment'"));
        }
        if ("unknown_granularity".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "unknown_granularity",
                            "message", "Granularity must be 'segment' or 'window'"));
        }
        if ("window_radius_out_of_range".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "window_radius_out_of_range",
                            "message", "windowRadius must be between 1 and "
                                    + com.coremasterkb.serving.domain.FullTextRequest.MAX_WINDOW_RADIUS));
        }
        if ("ref_id_required".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "ref_id_required", "message", "Each ref needs a non-blank id"));
        }
        // ---- 27号审查修复：scope hard filter 契约（显式拒绝优于静默忽略） ----
        if (ex.getMessage() != null && ex.getMessage().startsWith("unsupported_scope_filter:")) {
            String key = ex.getMessage().substring("unsupported_scope_filter:".length());
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "unsupported_scope_filter",
                            "message", "Filter key '" + key
                                    + "' is not supported yet. Supported: document_refs, "
                                    + "section_refs, evidence_types, asset_types"));
        }
        if (ex.getMessage() != null && ex.getMessage().startsWith("invalid_scope_ref")) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "invalid_scope_ref",
                            "message", "A document_refs/section_refs value could not be "
                                    + "resolved to an in-scope internal ref: " + ex.getMessage()));
        }
        // 29号 2.9：structured query DSL 未知键 → typed 400（不静默当空条件）
        if (ex.getMessage() != null && ex.getMessage().startsWith("unsupported_query_key:")) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "unsupported_query_key",
                            "message", "Unknown query keys: "
                                    + ex.getMessage().substring("unsupported_query_key:".length())
                                    + ". Allowed: select/where/order_by/limit/cursor/aggregate"));
        }
        // 29号 R06a：filter 值类型错误 → typed 400（不静默退化成宽检索）
        if (ex.getMessage() != null && ex.getMessage().startsWith("filter_value_invalid")) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "filter_value_invalid",
                            "message", ex.getMessage()));
        }
        if ("scope_ref_requires_kb".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "scope_ref_requires_kb",
                            "message", "Opaque doc_/st_ refs in scope filters require "
                                    + "an explicit knowledge base scope"));
        }
        // ---- R8（25 号 §7.1）显式请求参数校验 ----
        if ("top_k_invalid".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "top_k_invalid", "message", "top_k must be a positive integer"));
        }
        if ("expansion_invalid".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "expansion_invalid",
                            "message", "expansion.mode must be one of auto/exact/window/parent/whole_document"));
        }
        // Reached when a scope resolves to zero snapshots. Mapped explicitly because the
        // alternative — letting an empty snapshot list through — is an unfiltered read, so this
        // code existing at all is the visible half of that guard.
        if ("empty_scope".equals(ex.getMessage())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "empty_scope",
                            "message", "The requested scope contains no readable content"));
        }
        // Out of scope, nonexistent, and a stored path that escapes its KB directory all land
        // here with the same body — the caller learns nothing about which case it was.
        if ("document_not_found".equals(ex.getMessage())) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                    .body(Map.of("error", "document_not_found", "message", "Document not found"));
        }
        // Distinct from document_not_found: the document is visible, it simply has no original
        // file — legacy documents ingested through /api/runs never had one.
        if ("raw_file_unavailable".equals(ex.getMessage())) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                    .body(Map.of("error", "raw_file_unavailable",
                            "message", "This document has no original file available"));
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
        // A deployment problem, not a per-document one: serving.upload-root does not point at the
        // directory mining writes to. Kept separate from raw_file_unavailable so this shows up as
        // "the file store is wrong" rather than "none of your documents have files".
        if ("raw_file_storage_unavailable".equals(ex.getMessage())) {
            log.error("Raw file storage unavailable — serving.upload-root misconfigured");
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Map.of("error", "raw_file_storage_unavailable",
                            "message", "Document file storage is unavailable"));
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

    /**
     * A request body that could not be turned into its DTO.
     *
     * <p>Exists because request records validate in their compact constructors — {@code
     * SearchRequest} rejects a blank query, {@code FullTextRequest} rejects an unknown granularity
     * or an out-of-range radius, {@code FullTextRequest.Ref} rejects an unknown ref type. All of
     * those throw <em>during deserialization</em>, so Jackson wraps them and Spring re-wraps that
     * as {@link HttpMessageNotReadableException}. Without this the carefully mapped 400 codes above
     * were unreachable from the wire and every one of them surfaced as a 500 — the handler was
     * only ever exercised by tests that threw from a mocked service, which skips deserialization
     * entirely.</p>
     *
     * <p>Anything that is not one of our own validation failures (truncated JSON, wrong type for a
     * field) is a malformed request, which is still the caller's problem, not a server fault.</p>
     */
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<Map<String, Object>> handleUnreadableBody(HttpMessageNotReadableException ex) {
        for (Throwable cause = ex.getCause(); cause != null; cause = cause.getCause()) {
            if (cause instanceof IllegalArgumentException iae && iae.getMessage() != null) {
                return handleIllegalArgument(iae);
            }
        }
        log.warn("Malformed request body: {}", ex.getMostSpecificCause().getMessage());
        return ResponseEntity.badRequest()
                .body(Map.of("error", "malformed_request", "message", "Request body could not be parsed"));
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
