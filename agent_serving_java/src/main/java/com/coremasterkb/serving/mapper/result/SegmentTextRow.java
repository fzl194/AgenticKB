package com.coremasterkb.serving.mapper.result;

/**
 * 结构节点 ⋈ 原文切片行（asset_structure_nodes ⋈ asset_raw_segments，批次8 R5 hydrate）。
 *
 * <p>窗口/章节/文档展开的统一行形态：{@code parentRef} 是 segment 节点的父 section ref，
 * {@code ordinal} 即 segment_index（结构与原文以 snapshot_id + ordinal 连接）。</p>
 */
public class SegmentTextRow {

    private String snapshotId;
    private String ref;
    private String parentRef;
    private Integer ordinal;
    private String blockType;
    private String rawText;
    private Integer tokenCount;
    private String headingChainJson;

    public String getSnapshotId() { return snapshotId; }
    public void setSnapshotId(String snapshotId) { this.snapshotId = snapshotId; }

    public String getRef() { return ref; }
    public void setRef(String ref) { this.ref = ref; }

    public String getParentRef() { return parentRef; }
    public void setParentRef(String parentRef) { this.parentRef = parentRef; }

    public Integer getOrdinal() { return ordinal; }
    public void setOrdinal(Integer ordinal) { this.ordinal = ordinal; }

    public String getBlockType() { return blockType; }
    public void setBlockType(String blockType) { this.blockType = blockType; }

    public String getRawText() { return rawText; }
    public void setRawText(String rawText) { this.rawText = rawText; }

    public Integer getTokenCount() { return tokenCount; }
    public void setTokenCount(Integer tokenCount) { this.tokenCount = tokenCount; }

    public String getHeadingChainJson() { return headingChainJson; }
    public void setHeadingChainJson(String headingChainJson) { this.headingChainJson = headingChainJson; }
}
