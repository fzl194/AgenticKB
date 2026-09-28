package com.coremasterkb.serving.entity;

public class KnowledgeAccessRecord {
    private String id;
    private String occurredAt;
    private String completedAt;
    private String domain;
    private String actorUserId;
    private String actorUsername;
    private String source;
    private String operation;
    private String toolName;
    private String mcpKeyId;
    private String kbIdsJson;
    private String queryText;
    private String paradigmId;
    private Integer paradigmVersion;
    private String status;
    private Integer resultCount;
    private Integer durationMs;
    private String errorCode;
    private String detailsJson;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getOccurredAt() { return occurredAt; }
    public void setOccurredAt(String occurredAt) { this.occurredAt = occurredAt; }
    public String getCompletedAt() { return completedAt; }
    public void setCompletedAt(String completedAt) { this.completedAt = completedAt; }
    public String getDomain() { return domain; }
    public void setDomain(String domain) { this.domain = domain; }
    public String getActorUserId() { return actorUserId; }
    public void setActorUserId(String actorUserId) { this.actorUserId = actorUserId; }
    public String getActorUsername() { return actorUsername; }
    public void setActorUsername(String actorUsername) { this.actorUsername = actorUsername; }
    public String getSource() { return source; }
    public void setSource(String source) { this.source = source; }
    public String getOperation() { return operation; }
    public void setOperation(String operation) { this.operation = operation; }
    public String getToolName() { return toolName; }
    public void setToolName(String toolName) { this.toolName = toolName; }
    public String getMcpKeyId() { return mcpKeyId; }
    public void setMcpKeyId(String mcpKeyId) { this.mcpKeyId = mcpKeyId; }
    public String getKbIdsJson() { return kbIdsJson; }
    public void setKbIdsJson(String kbIdsJson) { this.kbIdsJson = kbIdsJson; }
    public String getQueryText() { return queryText; }
    public void setQueryText(String queryText) { this.queryText = queryText; }
    public String getParadigmId() { return paradigmId; }
    public void setParadigmId(String paradigmId) { this.paradigmId = paradigmId; }
    public Integer getParadigmVersion() { return paradigmVersion; }
    public void setParadigmVersion(Integer paradigmVersion) { this.paradigmVersion = paradigmVersion; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public Integer getResultCount() { return resultCount; }
    public void setResultCount(Integer resultCount) { this.resultCount = resultCount; }
    public Integer getDurationMs() { return durationMs; }
    public void setDurationMs(Integer durationMs) { this.durationMs = durationMs; }
    public String getErrorCode() { return errorCode; }
    public void setErrorCode(String errorCode) { this.errorCode = errorCode; }
    public String getDetailsJson() { return detailsJson; }
    public void setDetailsJson(String detailsJson) { this.detailsJson = detailsJson; }
}
