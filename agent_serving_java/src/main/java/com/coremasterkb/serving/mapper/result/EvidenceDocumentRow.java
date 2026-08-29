package com.coremasterkb.serving.mapper.result;

/**
 * snapshot → 文档/库投影行（asset_document_snapshot_links ⋈ asset_documents ⋈
 * knowledge_bases，批次8 R5 hydrate source projection）。
 *
 * <p>以 document_snapshot_id 为主键回查——{@code document_key}（doc:/path）非全局唯一
 * （不含 kb_id），跨库同名路径只能靠 snapshot 消歧。</p>
 */
public class EvidenceDocumentRow {

    private String snapshotId;
    private String documentKey;
    private String documentName;
    private String kbId;
    private String kbName;
    private String relativePath;

    public String getSnapshotId() { return snapshotId; }
    public void setSnapshotId(String snapshotId) { this.snapshotId = snapshotId; }

    public String getDocumentKey() { return documentKey; }
    public void setDocumentKey(String documentKey) { this.documentKey = documentKey; }

    public String getDocumentName() { return documentName; }
    public void setDocumentName(String documentName) { this.documentName = documentName; }

    public String getKbId() { return kbId; }
    public void setKbId(String kbId) { this.kbId = kbId; }

    public String getKbName() { return kbName; }
    public void setKbName(String kbName) { this.kbName = kbName; }

    public String getRelativePath() { return relativePath; }
    public void setRelativePath(String relativePath) { this.relativePath = relativePath; }
}
