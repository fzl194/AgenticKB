package com.coremasterkb.serving.operator.paradigm;

/**
 * Row of {@code operator_paradigm}: a paradigm's mutable metadata + editable draft graph.
 *
 * <p>瘦身批次5：域绑定字段（boundDomain/isDefault/boundAt）已删——批次6 起范式跨域通用，
 * 代码不再读写绑定；002 DDL 停止加载（存量库的列无害残留，待批次6 DB 审计一并 DROP）。</p>
 */
public class ParadigmEntity {

    private String id;
    private String name;
    private String description;
    private String draftGraphJson;   // JSONB stored/read as raw JSON text
    private int currentVersion;
    private String status;
    private String createdAt;
    private String updatedAt;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public String getDraftGraphJson() { return draftGraphJson; }
    public void setDraftGraphJson(String draftGraphJson) { this.draftGraphJson = draftGraphJson; }

    public int getCurrentVersion() { return currentVersion; }
    public void setCurrentVersion(int currentVersion) { this.currentVersion = currentVersion; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public String getCreatedAt() { return createdAt; }
    public void setCreatedAt(String createdAt) { this.createdAt = createdAt; }

    public String getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(String updatedAt) { this.updatedAt = updatedAt; }
}
