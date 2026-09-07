package com.coremasterkb.serving.mapper.result;

/**
 * A1 来源记录行（asset_source_locators，37/38 号）。
 *
 * <p>挖掘侧从 Parse IR 位置实值物化、随快照晋升；serving 只在
 * hydrate/assemble（Top-N）按 (snapshot, canonical representation) 点查——
 * 召回热路径不读。{@code locatorKind} 口径：page/line_range/sheet_cell 进
 * "来源可解析率"分母；native/section_only/unavailable 不进但有行有原因。</p>
 */
public class SourceLocatorRow {

    private String snapshotId;
    private String representationId;
    private String targetRef;
    private String documentRef;
    private String sourceFormat;
    private String locatorKind;
    private String sectionPath;
    private String sectionElementId;
    private Integer page;
    private Integer lineStart;
    private Integer lineEnd;
    private String sheet;
    private String cell;
    private String tableRef;
    private Integer rowIndex;
    private String nativeRefJson;
    private String description;

    public String getSnapshotId() { return snapshotId; }
    public void setSnapshotId(String snapshotId) { this.snapshotId = snapshotId; }

    public String getRepresentationId() { return representationId; }
    public void setRepresentationId(String representationId) { this.representationId = representationId; }

    public String getTargetRef() { return targetRef; }
    public void setTargetRef(String targetRef) { this.targetRef = targetRef; }

    public String getDocumentRef() { return documentRef; }
    public void setDocumentRef(String documentRef) { this.documentRef = documentRef; }

    public String getSourceFormat() { return sourceFormat; }
    public void setSourceFormat(String sourceFormat) { this.sourceFormat = sourceFormat; }

    public String getLocatorKind() { return locatorKind; }
    public void setLocatorKind(String locatorKind) { this.locatorKind = locatorKind; }

    public String getSectionPath() { return sectionPath; }
    public void setSectionPath(String sectionPath) { this.sectionPath = sectionPath; }

    public String getSectionElementId() { return sectionElementId; }
    public void setSectionElementId(String sectionElementId) { this.sectionElementId = sectionElementId; }

    public Integer getPage() { return page; }
    public void setPage(Integer page) { this.page = page; }

    public Integer getLineStart() { return lineStart; }
    public void setLineStart(Integer lineStart) { this.lineStart = lineStart; }

    public Integer getLineEnd() { return lineEnd; }
    public void setLineEnd(Integer lineEnd) { this.lineEnd = lineEnd; }

    public String getSheet() { return sheet; }
    public void setSheet(String sheet) { this.sheet = sheet; }

    public String getCell() { return cell; }
    public void setCell(String cell) { this.cell = cell; }

    public String getTableRef() { return tableRef; }
    public void setTableRef(String tableRef) { this.tableRef = tableRef; }

    public Integer getRowIndex() { return rowIndex; }
    public void setRowIndex(Integer rowIndex) { this.rowIndex = rowIndex; }

    public String getNativeRefJson() { return nativeRefJson; }
    public void setNativeRefJson(String nativeRefJson) { this.nativeRefJson = nativeRefJson; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
}
