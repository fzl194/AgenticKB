package com.coremasterkb.serving.operator.api;

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
        log.warn("[operator] runtime error: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(Map.of("error", "operator_error", "message", safe(ex.getMessage())));
    }

    private static String safe(String m) {
        return m != null ? m : "";
    }
}
