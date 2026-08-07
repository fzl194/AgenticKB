package com.coremasterkb.serving.operator.paradigm;

import java.util.List;

/**
 * A domain binding was rejected. Carries a stable {@code code} (surfaced as the response's
 * {@code error} field) rather than only a message, so the editor UI can branch on the reason.
 *
 * <p>{@code details} may name the offending items — e.g. the non-public knowledge bases that block
 * a binding. That is safe here in a way it is not at retrieval time: binding is an authenticated
 * management action on a paradigm the caller already administers, whereas
 * {@code KbAccessService} deliberately hides which KB ids exist from search callers.</p>
 */
public class ParadigmBindingException extends RuntimeException {

    private final String code;
    private final List<String> details;

    public ParadigmBindingException(String code, String message) {
        this(code, message, List.of());
    }

    public ParadigmBindingException(String code, String message, List<String> details) {
        super(message);
        this.code = code;
        this.details = (details != null) ? List.copyOf(details) : List.of();
    }

    public String code() {
        return code;
    }

    public List<String> details() {
        return details;
    }
}
