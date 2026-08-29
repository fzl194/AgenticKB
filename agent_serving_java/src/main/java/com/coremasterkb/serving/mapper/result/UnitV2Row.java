package com.coremasterkb.serving.mapper.result;

/**
 * Result row for v2 资产表召回（asset_retrieval_units_v2，批次8 R2）。
 *
 * <p>承载 §5.1 候选契约所需的列；{@code channelScore} 由通道 SQL 计算
 * （fts = ts_rank(search_vector, query)，dense = 1 - cosine distance）。</p>
 */
public class UnitV2Row {

    private String representationId;
    private String snapshotId;
    private String representationType;
    private String contentType;
    private String contentText;
    private String structuralContext;
    private String targetType;
    private String targetRef;
    private String canonicalEvidenceId;
    private String containerRef;
    private Integer ordinal;
    private String facetsJson;
    private double channelScore;

    public String getRepresentationId() { return representationId; }
    public void setRepresentationId(String representationId) { this.representationId = representationId; }

    public String getSnapshotId() { return snapshotId; }
    public void setSnapshotId(String snapshotId) { this.snapshotId = snapshotId; }

    public String getRepresentationType() { return representationType; }
    public void setRepresentationType(String representationType) { this.representationType = representationType; }

    public String getContentType() { return contentType; }
    public void setContentType(String contentType) { this.contentType = contentType; }

    public String getContentText() { return contentText; }
    public void setContentText(String contentText) { this.contentText = contentText; }

    public String getStructuralContext() { return structuralContext; }
    public void setStructuralContext(String structuralContext) { this.structuralContext = structuralContext; }

    public String getTargetType() { return targetType; }
    public void setTargetType(String targetType) { this.targetType = targetType; }

    public String getTargetRef() { return targetRef; }
    public void setTargetRef(String targetRef) { this.targetRef = targetRef; }

    public String getCanonicalEvidenceId() { return canonicalEvidenceId; }
    public void setCanonicalEvidenceId(String canonicalEvidenceId) { this.canonicalEvidenceId = canonicalEvidenceId; }

    public String getContainerRef() { return containerRef; }
    public void setContainerRef(String containerRef) { this.containerRef = containerRef; }

    public Integer getOrdinal() { return ordinal; }
    public void setOrdinal(Integer ordinal) { this.ordinal = ordinal; }

    public String getFacetsJson() { return facetsJson; }
    public void setFacetsJson(String facetsJson) { this.facetsJson = facetsJson; }

    public double getChannelScore() { return channelScore; }
    public void setChannelScore(double channelScore) { this.channelScore = channelScore; }
}
