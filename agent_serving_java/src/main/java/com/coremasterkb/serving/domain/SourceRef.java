package com.coremasterkb.serving.domain;

import java.util.Map;

/**
 * Reference to a source document.
 *
 * @param id           source identifier
 * @param documentKey  document key / path
 * @param title        document title
 * @param relativePath relative path within the knowledge base
 * @param kbId         owning knowledge base; null for legacy documents ingested through
 *                     {@code /api/runs}, which belong to no KB. Without this a caller cannot tell
 *                     which knowledge base an answer came from, nor address the document's
 *                     original file — both endpoints that do so are keyed by KB.
 * @param scopeJson    scoping metadata as JSON-compatible map; defaults to empty map
 * @param metadata     additional source metadata; defaults to empty map
 */
public record SourceRef(
        String id,
        String documentKey,
        String title,
        String relativePath,
        String kbId,
        Map<String, Object> scopeJson,
        Map<String, Object> metadata
) {
    public SourceRef {
        if (scopeJson == null) scopeJson = Map.of();
        if (metadata == null) metadata = Map.of();
    }

    /** Pre-{@code kbId} arity, kept so existing callers and tests need no change. */
    public SourceRef(String id, String documentKey, String title, String relativePath,
                     Map<String, Object> scopeJson, Map<String, Object> metadata) {
        this(id, documentKey, title, relativePath, null, scopeJson, metadata);
    }
}
