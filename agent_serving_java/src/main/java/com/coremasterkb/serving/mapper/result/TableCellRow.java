package com.coremasterkb.serving.mapper.result;

/**
 * asset_table_cells 行（批次8 R5 hydrate 表格回源）。
 */
public class TableCellRow {

    private String snapshotId;
    private String tableRef;
    private Integer rowIndex;
    private Integer columnIndex;
    private String columnName;
    private String value;
    private Boolean isHeader;
    /** A3（39 号 §3.2）：IR 声明的 cell 类型/规范值（存量行为 NULL → 列级回退扫描）。 */
    private String valueType;
    private String normalizedValue;

    public String getSnapshotId() { return snapshotId; }
    public void setSnapshotId(String snapshotId) { this.snapshotId = snapshotId; }

    public String getTableRef() { return tableRef; }
    public void setTableRef(String tableRef) { this.tableRef = tableRef; }

    public Integer getRowIndex() { return rowIndex; }
    public void setRowIndex(Integer rowIndex) { this.rowIndex = rowIndex; }

    public Integer getColumnIndex() { return columnIndex; }
    public void setColumnIndex(Integer columnIndex) { this.columnIndex = columnIndex; }

    public String getColumnName() { return columnName; }
    public void setColumnName(String columnName) { this.columnName = columnName; }

    public String getValueType() { return valueType; }
    public void setValueType(String valueType) { this.valueType = valueType; }

    public String getNormalizedValue() { return normalizedValue; }
    public void setNormalizedValue(String normalizedValue) { this.normalizedValue = normalizedValue; }

    public String getValue() { return value; }
    public void setValue(String value) { this.value = value; }

    public Boolean getIsHeader() { return isHeader; }
    public void setIsHeader(Boolean isHeader) { this.isHeader = isHeader; }
}
