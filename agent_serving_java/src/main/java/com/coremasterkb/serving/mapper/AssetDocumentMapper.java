package com.coremasterkb.serving.mapper;

import com.coremasterkb.serving.mapper.result.DocumentFileRow;
import com.coremasterkb.serving.mapper.result.DocumentSourceRow;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface AssetDocumentMapper {

    List<DocumentSourceRow> selectDocumentSources(
            @Param("documentIds") List<String> documentIds,
            @Param("snapshotIds") List<String> snapshotIds);

    /**
     * Resolve where each document's original uploaded file lives.
     *
     * <p>Confined to the snapshot scope via {@code asset_document_snapshot_links} so a caller
     * cannot learn a document's storage path by naming an id outside what they may read. The
     * scope filter is unconditional.</p>
     */
    List<DocumentFileRow> selectFileLocations(
            @Param("documentIds") List<String> documentIds,
            @Param("snapshotIds") List<String> snapshotIds);
}
