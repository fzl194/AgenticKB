package com.coremasterkb.serving.mapper.result;

/**
 * Where a document's original uploaded file lives on disk.
 *
 * <p>{@code kbId} and {@code storagePath} are both null for legacy documents — those ingested
 * through {@code /api/runs} rather than uploaded into a knowledge base. They have no original file
 * at all, which is why callers get {@code hasRawFile:false} rather than a broken download link.</p>
 */
public class DocumentFileRow {
    private String id;
    private String kbId;
    private String storagePath;
    private String documentName;
    private String documentKey;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }

    public String getKbId() { return kbId; }
    public void setKbId(String kbId) { this.kbId = kbId; }

    public String getStoragePath() { return storagePath; }
    public void setStoragePath(String storagePath) { this.storagePath = storagePath; }

    public String getDocumentName() { return documentName; }
    public void setDocumentName(String documentName) { this.documentName = documentName; }

    public String getDocumentKey() { return documentKey; }
    public void setDocumentKey(String documentKey) { this.documentKey = documentKey; }

    /** True when this document actually has an original file that can be streamed. */
    public boolean hasRawFile() {
        return kbId != null && !kbId.isBlank()
                && storagePath != null && !storagePath.isBlank();
    }
}
