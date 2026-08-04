package com.coremasterkb.serving.mapper;

import com.coremasterkb.serving.mapper.result.SectionPathCountRow;
import com.coremasterkb.serving.mapper.result.SegmentFullRow;
import com.coremasterkb.serving.mapper.result.SegmentWithMetaRow;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface AssetRawSegmentMapper {

    List<SegmentWithMetaRow> selectWithMeta(
            @Param("segmentIds") List<String> segmentIds,
            @Param("snapshotIds") List<String> snapshotIds);

    /**
     * Fetch segments' full uncompressed text for the full-text drill-down API.
     *
     * <p>Differs from {@link #selectWithMeta} in two ways that both matter:</p>
     * <ul>
     *   <li>It does not join {@code asset_document_snapshot_links}, so a segment yields exactly
     *       one row instead of one per linked document.</li>
     *   <li>The {@code snapshotIds} filter is <b>unconditional</b>. In {@code selectWithMeta} it
     *       sits inside an {@code <if>}, so an empty list silently means "no scope filter" — as a
     *       retrieval-internal call that is fine, but on an endpoint that takes ids straight from
     *       the caller it would be an unrestricted read of every knowledge base. Callers must
     *       reject an empty scope before reaching here; the mapper does not rescue them.</li>
     * </ul>
     */
    List<SegmentFullRow> selectFullByIds(
            @Param("segmentIds") List<String> segmentIds,
            @Param("snapshotIds") List<String> snapshotIds);

    List<SectionPathCountRow> selectSectionPathsByEntities(
            @Param("entityNames") List<String> entityNames,
            @Param("snapshotIds") List<String> snapshotIds);
}
