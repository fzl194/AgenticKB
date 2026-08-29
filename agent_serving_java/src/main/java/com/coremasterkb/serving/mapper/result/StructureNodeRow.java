package com.coremasterkb.serving.mapper.result;

/**
 * asset_structure_nodes 行（批次8 R5 hydrate 批量回源）。
 */
public class StructureNodeRow {

    private String snapshotId;
    private String nodeType;
    private String ref;
    private String parentRef;
    private Integer ordinal;
    private String title;
    private Integer level;
    private String blockType;

    public String getSnapshotId() { return snapshotId; }
    public void setSnapshotId(String snapshotId) { this.snapshotId = snapshotId; }

    public String getNodeType() { return nodeType; }
    public void setNodeType(String nodeType) { this.nodeType = nodeType; }

    public String getRef() { return ref; }
    public void setRef(String ref) { this.ref = ref; }

    public String getParentRef() { return parentRef; }
    public void setParentRef(String parentRef) { this.parentRef = parentRef; }

    public Integer getOrdinal() { return ordinal; }
    public void setOrdinal(Integer ordinal) { this.ordinal = ordinal; }

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public Integer getLevel() { return level; }
    public void setLevel(Integer level) { this.level = level; }

    public String getBlockType() { return blockType; }
    public void setBlockType(String blockType) { this.blockType = blockType; }
}
