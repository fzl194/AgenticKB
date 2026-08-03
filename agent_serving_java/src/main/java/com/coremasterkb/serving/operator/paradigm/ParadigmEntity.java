package com.coremasterkb.serving.operator.paradigm;

/**
 * Row of {@code operator_paradigm}: a paradigm's mutable metadata + editable draft graph.
 *
 * <p>The binding fields ({@code boundDomain}/{@code isDefault}/{@code boundAt}) are mutable
 * metadata, deliberately outside {@code operator_paradigm_version} — re-binding creates no new
 * version and never changes what a {@code (paradigmId, version)} call replays.</p>
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

    /** Domain this paradigm serves; null = unbound (still callable by id, excluded from matching). */
    private String boundDomain;
    /** Whether this is the domain's auto-matched paradigm. At most one live per domain (partial uq index). */
    private boolean isDefault;
    private String boundAt;

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

    // Explicit get/setIsDefault (not the isDefault() boolean convention) so the MyBatis
    // property name "isDefault" resolves unambiguously to setIsDefault.
    public String getBoundDomain() { return boundDomain; }
    public void setBoundDomain(String boundDomain) { this.boundDomain = boundDomain; }

    public boolean getIsDefault() { return isDefault; }
    public void setIsDefault(boolean isDefault) { this.isDefault = isDefault; }

    public String getBoundAt() { return boundAt; }
    public void setBoundAt(String boundAt) { this.boundAt = boundAt; }
}
