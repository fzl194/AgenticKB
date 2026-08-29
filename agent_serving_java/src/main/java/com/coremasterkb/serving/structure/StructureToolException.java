package com.coremasterkb.serving.structure;

import org.springframework.http.HttpStatus;

import java.util.Map;

/**
 * 结构工具族（批次8 R7，25 号 §7.2）的稳定 typed error。
 *
 * <p>错误码表与 Agent 下一步：</p>
 * <ul>
 *   <li>{@code invalid_ref}（400）——ref 格式非法；重新检索拿新 ref；</li>
 *   <li>{@code out_of_scope}（404）——ref 不在当前授权范围；<b>不泄漏</b>对象是否存在或属于
 *       哪个 KB（与 KbAccessService 的 kb_not_found 同语义：不存在与无权同响应）；</li>
 *   <li>{@code expired_ref}（410）——ref 合法且曾属于授权库，但所绑定快照已被新挖掘替换；
 *       重新 search 拿新 ref；</li>
 *   <li>{@code unknown_field}/{@code type_mismatch}/{@code unsupported_operation}（400）——
 *       structured query DSL 校验失败，details 携带允许字段/期望类型供 Agent 修正；</li>
 *   <li>{@code structured_query_unavailable}（409）——资产未过 readiness；退回 search /
 *       get_evidence；</li>
 *   <li>{@code result_too_large}（413）——缩小范围、增加 filter 或使用 cursor。</li>
 * </ul>
 *
 * <p>错误反馈是 Agent 闭环的一部分（§3.2），不得统一 500 或空数组。</p>
 */
public class StructureToolException extends RuntimeException {

    public static final String INVALID_REF = "invalid_ref";
    public static final String OUT_OF_SCOPE = "out_of_scope";
    public static final String EXPIRED_REF = "expired_ref";
    public static final String UNKNOWN_FIELD = "unknown_field";
    public static final String TYPE_MISMATCH = "type_mismatch";
    public static final String UNSUPPORTED_OPERATION = "unsupported_operation";
    public static final String STRUCTURED_QUERY_UNAVAILABLE = "structured_query_unavailable";
    public static final String RESULT_TOO_LARGE = "result_too_large";

    private final String code;
    private final HttpStatus status;
    private final Map<String, Object> details;

    public StructureToolException(String code, HttpStatus status, String message,
                                  Map<String, Object> details) {
        super(message);
        this.code = code;
        this.status = status;
        this.details = details == null ? Map.of() : Map.copyOf(details);
    }

    public String code() { return code; }
    public HttpStatus status() { return status; }
    public Map<String, Object> details() { return details; }

    // ---- factories（收敛语义与 HTTP 码的对应关系） --------------------------------

    public static StructureToolException invalidRef(String message) {
        return new StructureToolException(INVALID_REF, HttpStatus.BAD_REQUEST, message, Map.of());
    }

    /** 不泄漏真实归属：不存在 / 无权 / 跨用户同响应。 */
    public static StructureToolException outOfScope(String message) {
        return new StructureToolException(OUT_OF_SCOPE, HttpStatus.NOT_FOUND, message, Map.of());
    }

    public static StructureToolException expiredRef(String message) {
        return new StructureToolException(EXPIRED_REF, HttpStatus.GONE, message, Map.of());
    }

    public static StructureToolException unknownField(String field, java.util.List<String> allowed) {
        return new StructureToolException(UNKNOWN_FIELD, HttpStatus.BAD_REQUEST,
                "未知字段: " + field + "；可用字段见 details.allowed_fields（或先 inspect_knowledge）",
                Map.of("field", field, "allowed_fields", allowed));
    }

    public static StructureToolException typeMismatch(String field, String expected) {
        return new StructureToolException(TYPE_MISMATCH, HttpStatus.BAD_REQUEST,
                "字段 " + field + " 的值类型不符（期望 " + expected + "）",
                Map.of("field", field, "expected_type", expected));
    }

    public static StructureToolException unsupportedOperation(String message, Map<String, Object> details) {
        return new StructureToolException(UNSUPPORTED_OPERATION, HttpStatus.BAD_REQUEST, message, details);
    }

    public static StructureToolException structuredQueryUnavailable(String message) {
        return new StructureToolException(STRUCTURED_QUERY_UNAVAILABLE, HttpStatus.CONFLICT, message,
                Map.of("fallback", "search_knowledge / get_evidence"));
    }

    public static StructureToolException resultTooLarge(String message) {
        return new StructureToolException(RESULT_TOO_LARGE, HttpStatus.PAYLOAD_TOO_LARGE, message,
                Map.of("hint", "缩小范围、增加 filter 或使用 cursor 分页"));
    }
}
