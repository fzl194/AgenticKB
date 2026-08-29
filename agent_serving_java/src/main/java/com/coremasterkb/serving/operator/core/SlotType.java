package com.coremasterkb.serving.operator.core;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.domain.HydratedEvidence;
import com.coremasterkb.serving.domain.QueryUnderstanding;

import java.util.List;

/**
 * The slot type system for the pluggable retrieval operator engine.
 *
 * <p>Every operator input/output port (slot) is typed. Edges between operators are
 * validated at compile time (slot type compatibility) and again at runtime (value
 * class check). Each enum constant carries the concrete Java class flowing through it.</p>
 *
 * <p>Generic collection types ({@code STRING_LIST}, {@code CANDIDATE_LIST},
 * {@code CANDIDATE_LIST_MULTI}, {@code HYDRATED_EVIDENCE_LIST}) erase to
 * {@link java.util.List}; element typing is a convention enforced by the
 * producing/consuming operators, not by the JVM.</p>
 */
public enum SlotType {

    STRING(String.class),
    INT(Integer.class),
    DOUBLE(Double.class),
    BOOL(Boolean.class),
    VECTOR(float[].class),
    STRING_LIST(List.class),
    /** {@code List<RetrievalCandidate>} — the main flow type between retrieve/fuse/rerank. */
    CANDIDATE_LIST(List.class),
    /** Variadic candidate input: a fusion operator may accept multiple upstream CANDIDATE_LIST edges. */
    CANDIDATE_LIST_MULTI(List.class),
    /** {@code List<HydratedEvidence>} — evidence_hydrate → assemble (批次8 R5). */
    HYDRATED_EVIDENCE_LIST(List.class),
    /** {@link EvidenceResponse} — the only public protocol terminus (批次8 R6, 25 号 §5.3). */
    EVIDENCE_RESPONSE(EvidenceResponse.class),
    SCOPE(ActiveScope.class),
    QUERY_UNDERSTANDING(QueryUnderstanding.class);

    private final Class<?> javaType;

    SlotType(Class<?> javaType) {
        this.javaType = javaType;
    }

    public Class<?> javaType() {
        return javaType;
    }

    /** Runtime check: a null value is always accepted; otherwise the value must be an instance of {@link #javaType()}. */
    public boolean matches(Object value) {
        return value == null || javaType.isInstance(value);
    }

    /**
     * Compile-time edge compatibility: can a value produced as {@code from} be fed into a {@code to} input slot?
     * Identical types are compatible; a single {@link #CANDIDATE_LIST} may also feed a variadic
     * {@link #CANDIDATE_LIST_MULTI} input.
     */
    public static boolean isAssignable(SlotType from, SlotType to) {
        if (from == to) {
            return true;
        }
        return to == CANDIDATE_LIST_MULTI && from == CANDIDATE_LIST;
    }
}
