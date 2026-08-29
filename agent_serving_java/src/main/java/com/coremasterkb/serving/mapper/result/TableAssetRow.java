package com.coremasterkb.serving.mapper.result;

/**
 * asset_structured_assets 行（批次8 R5 hydrate 表格资产回源）。
 */
public class TableAssetRow {

    private String snapshotId;
    private String assetRef;
    private String assetType;
    private String tableRef;
    private String columnsJson;
    private Integer rowCount;
    private String readiness;
    private String schemaVersion;

    public String getSnapshotId() { return snapshotId; }
    public void setSnapshotId(String snapshotId) { this.snapshotId = snapshotId; }

    public String getAssetRef() { return assetRef; }
    public void setAssetRef(String assetRef) { this.assetRef = assetRef; }

    public String getAssetType() { return assetType; }
    public void setAssetType(String assetType) { this.assetType = assetType; }

    public String getTableRef() { return tableRef; }
    public void setTableRef(String tableRef) { this.tableRef = tableRef; }

    public String getColumnsJson() { return columnsJson; }
    public void setColumnsJson(String columnsJson) { this.columnsJson = columnsJson; }

    public Integer getRowCount() { return rowCount; }
    public void setRowCount(Integer rowCount) { this.rowCount = rowCount; }

    public String getReadiness() { return readiness; }
    public void setReadiness(String readiness) { this.readiness = readiness; }

    public String getSchemaVersion() { return schemaVersion; }
    public void setSchemaVersion(String schemaVersion) { this.schemaVersion = schemaVersion; }
}
