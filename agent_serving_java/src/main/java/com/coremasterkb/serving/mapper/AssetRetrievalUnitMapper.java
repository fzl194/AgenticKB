package com.coremasterkb.serving.mapper;

import com.coremasterkb.serving.mapper.result.FtsResultRow;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 旧 asset_retrieval_units 检索面（瘦身后仅剩研究辅助）。
 *
 * <p>瘦身批次4：正式 FTS/hydrate 已由范式链的 AssetRetrievalUnitV2Mapper +
 * EvidenceSourceV2Mapper 承担；fetchDetailsByIdsInScope 与 Trigram/Like/WithScope
 * 变体随 fulltext/raw HTTP 链退役。保留下列两个方法：</p>
 * <ul>
 *   <li>{@link #searchByFts} —— 实体研究 IT（AssetRawSegmentMapperIT 等）取作用域内
 *       unit 的辅助查询；</li>
 *   <li>{@link #searchByEntityExact} —— 实体精确检索（实体/本体研究线）。</li>
 * </ul>
 */
public interface AssetRetrievalUnitMapper {

    /**
     * tsvector full-text search ("token1 OR token2") with 'simple' dictionary.
     */
    List<FtsResultRow> searchByFts(
            @Param("ftsQuery") String ftsQuery,
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("limit") int limit);

    // ----- Entity exact -----

    /**
     * Entity-exact search: returns units whose entity_refs_json contains
     * any element with a "name" field matching one of the given entityNames.
     * When entityContainmentParams is non-empty, uses JSONB @> containment
     * (GIN-indexable) instead of jsonb_array_elements.
     */
    List<FtsResultRow> searchByEntityExact(
            @Param("entityNames") List<String> entityNames,
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("entityContainmentParams") List<String> entityContainmentParams,
            @Param("sectionPrefixes") List<String> sectionPrefixes,
            @Param("limit") int limit);
}
