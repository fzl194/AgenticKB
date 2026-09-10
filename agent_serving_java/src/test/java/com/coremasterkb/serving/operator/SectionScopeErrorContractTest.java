package com.coremasterkb.serving.operator;

import com.coremasterkb.serving.api.GlobalExceptionHandler;
import com.coremasterkb.serving.operator.api.OperatorExceptionHandler;
import com.coremasterkb.serving.operator.core.exceptions.OperatorException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * P2-16 回归：{@code section_scope_too_broad} 必须映射为 typed 400。
 *
 * <p>缺陷：ScopeResolveOperator 抛
 * {@code IllegalArgumentException("section_scope_too_broad: …")}，但该码
 * 不在 OperatorExceptionHandler 的 caller-fixable 白名单——被包装成 500
 * {@code operator_error}，违反「可修正错误不得 500」的需求（FTS/dense/
 * 公共 REST 同一契约）。</p>
 */
@DisplayName("section_scope_too_broad maps to typed 400, not 500")
class SectionScopeErrorContractTest {

    private final OperatorExceptionHandler handler = new OperatorExceptionHandler(
            new GlobalExceptionHandler());

    private ResponseEntity<Map<String, Object>> runOperatorFailure(String message) {
        OperatorException ex = new OperatorException(
                "scope_resolve failed", new IllegalArgumentException(message));
        return handler.handleOperator(ex);
    }

    @Test
    @DisplayName("scope-too-broad surfaces as 400 caller-fixable, not 500")
    void scopeTooBroadIs400() {
        ResponseEntity<Map<String, Object>> out = runOperatorFailure(
                "section_scope_too_broad: 范围章节超过 64 个，请缩小范围");
        assertThat(out.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(String.valueOf(out.getBody().get("error")))
                .contains("section_scope_too_broad");
    }

    @Test
    @DisplayName("per-root descendant limit variant also 400")
    void perRootLimitVariantIs400() {
        ResponseEntity<Map<String, Object>> out = runOperatorFailure(
                "section_scope_too_broad: 章节 doc.md#section:0 的子树超过 512 个节点，请缩小范围");
        assertThat(out.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    @DisplayName("unrelated IAE still 500 (whitelist must not widen)")
    void unrelatedIaeStays500() {
        ResponseEntity<Map<String, Object>> out = runOperatorFailure(
                "some internal invariant broke");
        assertThat(out.getStatusCode()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);
    }
}
