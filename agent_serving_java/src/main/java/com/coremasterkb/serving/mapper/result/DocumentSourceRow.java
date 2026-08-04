package com.coremasterkb.serving.mapper.result;

public class DocumentSourceRow {
    private String id;
    private String documentKey;
    private String relativePath;
    private String title;
    private String scopeJson;
    private String kbId;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }

    public String getKbId() { return kbId; }
    public void setKbId(String kbId) { this.kbId = kbId; }

    public String getDocumentKey() { return documentKey; }
    public void setDocumentKey(String documentKey) { this.documentKey = documentKey; }

    public String getRelativePath() { return relativePath; }
    public void setRelativePath(String relativePath) { this.relativePath = relativePath; }

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public String getScopeJson() { return scopeJson; }
    public void setScopeJson(String scopeJson) { this.scopeJson = scopeJson; }
}
