package com.coremasterkb.serving.operator.core;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.HydratedEvidence;
import com.coremasterkb.serving.domain.QueryUnderstanding;
import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.operator.core.exceptions.SlotTypeMismatchException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * A typed bag of slot name → value, used both for an operator's inputs and its outputs.
 *
 * <p>Values are validated against a declared {@link SlotType} on the way in (when bound by the
 * engine) and can be re-validated on read via {@link #getChecked(SlotDecl)}. Convenience typed
 * getters perform the unchecked casts that erased generics force, centralising them here.</p>
 */
public final class SlotValues {

    private final Map<String, Object> values = new LinkedHashMap<>();

    public SlotValues() {}

    public static SlotValues of(String slotName, Object value) {
        SlotValues sv = new SlotValues();
        sv.put(slotName, value);
        return sv;
    }

    public SlotValues put(String slotName, Object value) {
        values.put(slotName, value);
        return this;
    }

    public boolean has(String slotName) {
        return values.containsKey(slotName);
    }

    public Object get(String slotName) {
        return values.get(slotName);
    }

    public Set<String> names() {
        return values.keySet();
    }

    /**
     * Read a slot value, validating it against the declaration's {@link SlotType}.
     * Returns {@code null} for an absent optional slot; throws for an absent required slot
     * or a type mismatch.
     */
    public Object getChecked(SlotDecl decl) {
        Object v = values.get(decl.name());
        if (v == null) {
            if (decl.required()) {
                throw new SlotTypeMismatchException(
                        "required slot '" + decl.name() + "' (" + decl.type() + ") is missing");
            }
            return null;
        }
        if (!decl.type().matches(v)) {
            throw new SlotTypeMismatchException(
                    "slot '" + decl.name() + "' expected " + decl.type()
                            + " but got " + v.getClass().getSimpleName());
        }
        return v;
    }

    // ---- convenience typed accessors -------------------------------------------------------

    public String getString(String slotName) {
        Object v = values.get(slotName);
        return v != null ? v.toString() : null;
    }

    public float[] getVector(String slotName) {
        Object v = values.get(slotName);
        return v instanceof float[] arr ? arr : null;
    }

    public ActiveScope getScope(String slotName) {
        Object v = values.get(slotName);
        return v instanceof ActiveScope s ? s : null;
    }

    public QueryUnderstanding getUnderstanding(String slotName) {
        Object v = values.get(slotName);
        return v instanceof QueryUnderstanding qu ? qu : null;
    }

    @SuppressWarnings("unchecked")
    public List<String> getStringList(String slotName) {
        Object v = values.get(slotName);
        return v instanceof List<?> list ? (List<String>) list : List.of();
    }

    @SuppressWarnings("unchecked")
    public List<RetrievalCandidate> getCandidates(String slotName) {
        Object v = values.get(slotName);
        return v instanceof List<?> list ? (List<RetrievalCandidate>) list : List.of();
    }

    @SuppressWarnings("unchecked")
    public List<HydratedEvidence> getHydratedEvidence(String slotName) {
        Object v = values.get(slotName);
        return v instanceof List<?> list ? (List<HydratedEvidence>) list : List.of();
    }
}
