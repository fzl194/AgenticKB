package com.coremasterkb.serving.mapper;

import com.coremasterkb.serving.entity.AssetBuildDocumentSnapshot;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface AssetBuildDocumentSnapshotMapper {

    List<AssetBuildDocumentSnapshot> selectByBuildIdAndStatus(
            @Param("buildId") String buildId,
            @Param("selectionStatus") String selectionStatus);

    /**
     * Resolve the current snapshot of every document owned by the given knowledge bases.
     *
     * <p>Used by the KB-narrowed scope path, which cannot go through releases: KB mining runs with
     * {@code publish=false}, so KB content only ever reaches a build, never a release.</p>
     *
     * <p>Per document, the winner is the newest build <em>of that document's own KB</em> that
     * contains it; a document whose winning selection is {@code removed} is dropped rather than
     * falling back to an older {@code active} row. Callers get only {@code active} selections.</p>
     *
     * @param domain the routed domain; guards against a kb id from another domain resolving
     *               against a shared physical database
     * @param kbIds  non-empty, pre-normalized list of knowledge base ids
     */
    List<AssetBuildDocumentSnapshot> selectLatestKbSnapshots(
            @Param("domain") String domain,
            @Param("kbIds") List<String> kbIds);
}
