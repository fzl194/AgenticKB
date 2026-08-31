package com.coremasterkb.serving.operator.api;

import com.coremasterkb.serving.api.GlobalExceptionHandler;
import com.coremasterkb.serving.operator.core.exceptions.OperatorException;
import com.coremasterkb.serving.operator.core.exceptions.ParadigmCompileException;
import com.coremasterkb.serving.operator.paradigm.ParadigmBadRequestException;
import com.coremasterkb.serving.operator.paradigm.ParadigmNotFoundException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Exception mapping for the operator subsystem. Separate from the existing
 * {@code GlobalExceptionHandler} (which is left untouched); Spring resolves each exception to the
 * most specific handler across all advices, so these operator-specific types are handled here while
 * everything else still flows to the existing handler.
 */
@RestControllerAdvice
@Order(0)
public class OperatorExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(OperatorExceptionHandler.class);

    /**
     * Caller-fixable codes that may surface from inside the paradigm pipeline. The executor wraps
     * <em>every</em> node failure in an {@link OperatorException}, which used to collapse these
     * parameter mistakes (wrong domain → {@code kb_not_found} and friends) into a bare 500
     * {@code operator_error} — unreachable as the carefully mapped 4xx codes they already have in
     * {@code GlobalExceptionHandler}. Unwrapped here and delegated so the wire shape is identical
     * to the direct API; unknown causes stay 500.
     */
    private static final List<String> CALLER_FIXABLE_CODES = List.of(
            "kb_not_found", "no_active_kb_build", "empty_scope", "kb_ids_required",
            "top_k_invalid", "expansion_invalid", "query_required",
            "unknown_domain", "domain_disabled");

    private static final List<String> CALLER_FIXABLE_PREFIXES = List.of(
            "unsupported_scope_filter:", "invalid_scope_ref", "unsupported_query_key:",
            "filter_value_invalid", "scope_ref_requires_kb");

    private final GlobalExceptionHandler globalExceptionHandler;

    public OperatorExceptionHandler(GlobalExceptionHandler globalExceptionHandler) {
        this.globalExceptionHandler = globalExceptionHandler;
    }

    @ExceptionHandler(ParadigmCompileException.class)
    public ResponseEntity<Map<String, Object>> handleCompile(ParadigmCompileException ex) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error", "paradigm_compile_failed");
        body.put("errors", ex.errors());
        return ResponseEntity.badRequest().body(body);
    }

    @ExceptionHandler(ParadigmNotFoundException.class)
    public ResponseEntity<Map<String, Object>> handleNotFound(ParadigmNotFoundException ex) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Map.of("error", "paradigm_not_found", "message", safe(ex.getMessage())));
    }


    @ExceptionHandler(ParadigmBadRequestException.class)
    public ResponseEntity<Map<String, Object>> handleBadRequest(ParadigmBadRequestException ex) {
        return ResponseEntity.badRequest()
                .body(Map.of("error", "bad_request", "message", safe(ex.getMessage())));
    }

    @ExceptionHandler(OperatorException.class)
    public ResponseEntity<Map<String, Object>> handleOperator(OperatorException ex) {
        IllegalArgumentException callerFixable = findCallerFixableCause(ex);
        if (callerFixable != null) {
            log.warn("[operator] caller-fixable failure: {}", callerFixable.getMessage());
            return globalExceptionHandler.handleIllegalArgument(callerFixable);
        }
        log.warn("[operator] runtime error: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(Map.of("error", "operator_error", "message", safe(ex.getMessage())));
    }

    /**
     * Walk the cause chain for a known caller-fixable {@link IllegalArgumentException}; null when
     * the failure is genuinely ours. Restricting to the known list matters: an internal bug that
     * happens to throw IAE must still surface as 500, not masquerade as a client error.
     */
    private static IllegalArgumentException findCallerFixableCause(OperatorException ex) {
        for (Throwable cause = ex.getCause(); cause != null; cause = cause.getCause()) {
            if (cause instanceof IllegalArgumentException iae && isCallerFixable(iae.getMessage())) {
                return iae;
            }
        }
        return null;
    }

    private static boolean isCallerFixable(String message) {
        if (message == null) {
            return false;
        }
        for (String code : CALLER_FIXABLE_CODES) {
            if (code.equals(message)) {
                return true;
            }
        }
        for (String prefix : CALLER_FIXABLE_PREFIXES) {
            if (message.startsWith(prefix)) {
                return true;
            }
        }
        return false;
    }

    private static String safe(String m) {
        return m != null ? m : "";
    }
}
