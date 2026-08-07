package com.coremasterkb.serving.mapper;

import com.coremasterkb.serving.mapper.result.FtsResultRow;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface AssetRetrievalUnitMapper {

    /**
     * Fetch heavy columns (text, source_refs_json, target_ref_json) for specific IDs.
     * Used for hydration after fusion + rerank to avoid transferring large text
     * during the retrieval phase.
     */
    List<FtsResultRow> fetchDetailsByIds(@Param("ids") List<String> ids);

    /**
     * Same as {@link #fetchDetailsByIds} but confined to a snapshot scope, and returning the
     * columns the full-text drill-down needs (title / unit_type / document_snapshot_id).
     *
     * <p>{@link #fetchDetailsByIds} has no scope filter at all: it is called during hydration,
     * where the ids already came out of a scope-filtered retrieval. Exposing that method on an
     * endpoint would let any caller read any unit in any knowledge base by naming its id, so the
     * drill-down uses this one instead. The unfiltered method is left alone rather than tightened
     * — changing its signature would ripple through the retrieval path for no benefit.</p>
     */
    List<FtsResultRow> fetchDetailsByIdsInScope(
            @Param("ids") List<String> ids,
            @Param("snapshotIds") List<String> snapshotIds);

    // ----- Level 1: tsvector full-text search -----

    List<FtsResultRow> searchByFts(
            @Param("ftsQuery") String ftsQuery,
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("limit") int limit);

    /** tsvector search with scope filter (facets_json JSONB containment). */
    List<FtsResultRow> searchByFtsWithScope(
            @Param("ftsQuery") String ftsQuery,
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("scopeJsonParams") List<String> scopeJsonParams,
            @Param("sectionPrefixes") List<String> sectionPrefixes,
            @Param("limit") int limit);

    // ----- Level 2: pg_trgm trigram similarity -----

    /** Trigram similarity fallback without scope. */
    List<FtsResultRow> searchByTrigram(
            @Param("queryText") String queryText,
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("limit") int limit);

    /** Trigram similarity with scope filter. */
    List<FtsResultRow> searchByTrigramWithScope(
            @Param("queryText") String queryText,
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("scopeJsonParams") List<String> scopeJsonParams,
            @Param("sectionPrefixes") List<String> sectionPrefixes,
            @Param("limit") int limit);

    // ----- Level 3: LIKE fallback -----

    /** LIKE fallback without scope. */
    List<FtsResultRow> searchByLike(
            @Param("likeTerms") List<String> likeTerms,
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("limit") int limit);

    /** LIKE fallback with scope filter. */
    List<FtsResultRow> searchByLikeWithScope(
            @Param("likeTerms") List<String> likeTerms,
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("scopeJsonParams") List<String> scopeJsonParams,
            @Param("sectionPrefixes") List<String> sectionPrefixes,
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
