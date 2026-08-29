package com.coremasterkb.serving.operator.mapper;

import com.coremasterkb.serving.mapper.result.EvidenceDocumentRow;
import com.coremasterkb.serving.mapper.result.SegmentTextRow;
import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.mapper.result.TableAssetRow;
import com.coremasterkb.serving.mapper.result.TableCellRow;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 批次8 R5 {@code evidence_hydrate} 批量回源 mapper（25 号 §6.8/§10.2）。
 *
 * <p>DDL 真相源：mining {@code retrieval_projection/schema.py}（v2 资产表）+ KB 管理表
 * （mining {@code kb/db.py}）。全部查询按 snapshot_id 分组批量下推，禁止 candidate N+1、
 * 禁止全库扫描；章节/文档/表格展开用 {@code row_number() OVER (PARTITION BY ...)} 保证
 * 每组行数有界。</p>
 */
public interface EvidenceSourceV2Mapper {

    /**
     * 邻窗锚点：(snapshot, parentRef) 下以 centerOrdinal 为中心、半径 radius 的有界窗口。
     */
    record WindowAnchor(String snapshotId, String parentRef, int centerOrdinal, int radius) {}

    /**
     * canonical 的源表示（exact 内容 + structural_context）：只取 returnable=TRUE 的表示，
     * query/summary alias（returnable=FALSE）天然被排除——别名命中经此查询即"回源"。
     */
    List<UnitV2Row> selectCanonicalRepresentations(
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("canonicalIds") List<String> canonicalIds);

    /** 结构节点批量（segment 的 parent/ordinal、section 的 title、table 节点）。 */
    List<StructureNodeRow> selectStructureNodes(
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("refs") List<String> refs);

    /**
     * 有界邻窗：每锚点 (2·radius+1) 行，按 (snapshot, parent, ordinal) 有序。
     * OR-per-anchor 形式保证一次批量查询覆盖全部锚点（窗口在 SQL 内收窄，不拉全 section）。
     */
    List<SegmentTextRow> selectWindowSegments(@Param("anchors") List<WindowAnchor> anchors);

    /**
     * 章节/文档直接子 segment：每个 parent_ref 至多 {@code maxRowsPerSection} 行
     * （row_number 分区截断），按 (snapshot, parent, ordinal) 有序。
     */
    List<SegmentTextRow> selectSectionSegments(
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("parentRefs") List<String> parentRefs,
            @Param("maxRowsPerSection") int maxRowsPerSection);

    /**
     * 整文 segment：每个 snapshot 至多 {@code maxRowsPerDocument} 行（row_number 分区截断），
     * 按源 ordinal 有序。整文 fits 判定用 {@link #selectDocumentTokenTotals} 先行。
     */
    List<SegmentTextRow> selectDocumentSegments(
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("maxRowsPerDocument") int maxRowsPerDocument);

    /** 每 snapshot 的原文 token 总量（whole_document fits 判定；无行 = 0）。 */
    List<SnapshotTokensRow> selectDocumentTokenTotals(@Param("snapshotIds") List<String> snapshotIds);

    /** snapshot token 总量行。 */
    record SnapshotTokensRow(String snapshotId, Long totalTokens) {}

    /** 表格资产（表头 columns_json/caption 就绪度）批量。 */
    List<TableAssetRow> selectTableAssets(
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("tableRefs") List<String> tableRefs);

    /**
     * 表格 cells：每表至多 {@code maxCellsPerTable} 个（row_number 分区截断），按
     * (row, column) 有序。表头优先（is_header DESC），命中行/有界整表共用。
     */
    List<TableCellRow> selectTableCells(
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("tableRefs") List<String> tableRefs,
            @Param("maxCellsPerTable") int maxCellsPerTable);

    /** snapshot → 文档/库投影（file_name/relative_path/kb 名称，source projection 用）。 */
    List<EvidenceDocumentRow> selectDocumentSources(@Param("snapshotIds") List<String> snapshotIds);
}
