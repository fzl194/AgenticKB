package com.coremasterkb.serving.mapper;

import com.coremasterkb.serving.mapper.result.SectionPathCountRow;
import com.coremasterkb.serving.mapper.result.SegmentWithMetaRow;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 旧 asset_raw_segments 读取面（实体/关系研究线保留）。
 *
 * <p>瘦身批次4：仅服务旧 fulltext 下钻链的 selectFullByIds/selectWindows 已随
 * FullTextService 退役；下列方法留给实体/本体研究线使用。</p>
 */
public interface AssetRawSegmentMapper {

    /**
     * Segment rows with document/section metadata. The {@code snapshotIds} filter sits
     * inside an {@code <if>}: an empty list means "no scope filter" — retrieval-internal
     * semantics, callers must pre-scope.
     */
    List<SegmentWithMetaRow> selectWithMeta(
            @Param("segmentIds") List<String> segmentIds,
            @Param("snapshotIds") List<String> snapshotIds);

    List<SectionPathCountRow> selectSectionPathsByEntities(
            @Param("entityNames") List<String> entityNames,
            @Param("snapshotIds") List<String> snapshotIds);
}
