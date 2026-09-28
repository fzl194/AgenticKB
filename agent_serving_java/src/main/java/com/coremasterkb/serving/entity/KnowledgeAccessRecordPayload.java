package com.coremasterkb.serving.entity;

public class KnowledgeAccessRecordPayload {
    private String recordId;
    private String requestJson;
    private String effectiveContextJson;
    private String responseMode;
    private String responseJson;
    private String responseRefsJson;
    private Integer requestBytes;
    private Integer responseBytes;
    private boolean responseTruncated;
    private Integer responseOriginalBytes;
    private int responseOmittedCount;
    private String responseSha256;
    private String redactionsJson;
    private int payloadSchemaVersion;

    public String getRecordId() { return recordId; }
    public void setRecordId(String recordId) { this.recordId = recordId; }
    public String getRequestJson() { return requestJson; }
    public void setRequestJson(String requestJson) { this.requestJson = requestJson; }
    public String getEffectiveContextJson() { return effectiveContextJson; }
    public void setEffectiveContextJson(String effectiveContextJson) { this.effectiveContextJson = effectiveContextJson; }
    public String getResponseMode() { return responseMode; }
    public void setResponseMode(String responseMode) { this.responseMode = responseMode; }
    public String getResponseJson() { return responseJson; }
    public void setResponseJson(String responseJson) { this.responseJson = responseJson; }
    public String getResponseRefsJson() { return responseRefsJson; }
    public void setResponseRefsJson(String responseRefsJson) { this.responseRefsJson = responseRefsJson; }
    public Integer getRequestBytes() { return requestBytes; }
    public void setRequestBytes(Integer requestBytes) { this.requestBytes = requestBytes; }
    public Integer getResponseBytes() { return responseBytes; }
    public void setResponseBytes(Integer responseBytes) { this.responseBytes = responseBytes; }
    public boolean isResponseTruncated() { return responseTruncated; }
    public void setResponseTruncated(boolean responseTruncated) { this.responseTruncated = responseTruncated; }
    public Integer getResponseOriginalBytes() { return responseOriginalBytes; }
    public void setResponseOriginalBytes(Integer responseOriginalBytes) { this.responseOriginalBytes = responseOriginalBytes; }
    public int getResponseOmittedCount() { return responseOmittedCount; }
    public void setResponseOmittedCount(int responseOmittedCount) { this.responseOmittedCount = responseOmittedCount; }
    public String getResponseSha256() { return responseSha256; }
    public void setResponseSha256(String responseSha256) { this.responseSha256 = responseSha256; }
    public String getRedactionsJson() { return redactionsJson; }
    public void setRedactionsJson(String redactionsJson) { this.redactionsJson = redactionsJson; }
    public int getPayloadSchemaVersion() { return payloadSchemaVersion; }
    public void setPayloadSchemaVersion(int payloadSchemaVersion) { this.payloadSchemaVersion = payloadSchemaVersion; }
}
