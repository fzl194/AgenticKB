package com.coremasterkb.serving.mapper.result;

/**
 * A raw segment with its full, uncompressed text and locating metadata.
 *
 * <p>Deliberately <em>not</em> {@link SegmentWithMetaRow}: that row carries document identity by
 * joining {@code asset_document_snapshot_links}, which is 1:N (snapshots are content-deduplicated
 * via {@code UNIQUE (domain, normalized_content_hash)}, so one snapshot can belong to several
 * documents) and therefore fans a single segment out into duplicate rows. Here document attribution
 * is resolved in the service layer from the request's own {@code ActiveScope.documentSnapshotMap},
 * which both de-duplicates and confines the answer to what the caller may see.</p>
 */
public class SegmentFullRow {
    private String id;
    private String documentSnapshotId;
    private String segmentKey;
    private Integer segmentIndex;
    private String rawText;
    private String blockType;
    private String semanticRole;
    private String sectionPath;
    private String sectionTitle;
    private Integer tokenCount;
    private String snapshotTitle;
    private String metadataJson;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }

    public String getDocumentSnapshotId() { return documentSnapshotId; }
    public void setDocumentSnapshotId(String documentSnapshotId) { this.documentSnapshotId = documentSnapshotId; }

    public String getSegmentKey() { return segmentKey; }
    public void setSegmentKey(String segmentKey) { this.segmentKey = segmentKey; }

    public Integer getSegmentIndex() { return segmentIndex; }
    public void setSegmentIndex(Integer segmentIndex) { this.segmentIndex = segmentIndex; }

    public String getRawText() { return rawText; }
    public void setRawText(String rawText) { this.rawText = rawText; }

    public String getBlockType() { return blockType; }
    public void setBlockType(String blockType) { this.blockType = blockType; }

    public String getSemanticRole() { return semanticRole; }
    public void setSemanticRole(String semanticRole) { this.semanticRole = semanticRole; }

    public String getSectionPath() { return sectionPath; }
    public void setSectionPath(String sectionPath) { this.sectionPath = sectionPath; }

    public String getSectionTitle() { return sectionTitle; }
    public void setSectionTitle(String sectionTitle) { this.sectionTitle = sectionTitle; }

    public Integer getTokenCount() { return tokenCount; }
    public void setTokenCount(Integer tokenCount) { this.tokenCount = tokenCount; }

    public String getSnapshotTitle() { return snapshotTitle; }
    public void setSnapshotTitle(String snapshotTitle) { this.snapshotTitle = snapshotTitle; }

    public String getMetadataJson() { return metadataJson; }
    public void setMetadataJson(String metadataJson) { this.metadataJson = metadataJson; }
}
