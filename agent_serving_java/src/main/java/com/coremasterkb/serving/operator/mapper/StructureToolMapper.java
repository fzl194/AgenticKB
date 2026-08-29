package com.coremasterkb.serving.operator.mapper;

import com.coremasterkb.serving.mapper.result.SegmentTextRow;
import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.mapper.result.TableAssetRow;
import com.coremasterkb.serving.mapper.result.TableCellRow;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 批次8 R7 结构工具族 mapper（25 号 §6.10/§6.11：structure_navigate / structured_query /
 * inspect / get_evidence / get_document）。
 *
 * <p>DDL 真相源同 {@link EvidenceSourceV2Mapper}（mining retrieval_projection/schema.py）。
 * 全部查询以 (snapshot_id, ref) 为主键定位（ref 已先经 HMAC 反查 + 授权校验），深度/行数
 * 上限在 SQL 内收窄（递归 CTE 带 maxDepth、LIMIT 参数化），禁止无界遍历。</p>
 *
 * <p>structured query 的行/聚合 SQL 是<b>固定骨架 + 参数绑定</b>：字段名只作
 * {@code cells->>#{field}} 绑定值（先经 Java 白名单校验），操作符/排序方向/聚合函数来自
 * <choose> 固定分支（枚举匹配），无任何 {@code ${}} 用户输入插值。</p>
 */
public interface StructureToolMapper {

    /** ref 反查候选行：(snapshot_id, 内部 ref)。 */
    record RefRow(String snapshotId, String ref) {}

    /** 结构边行。 */
    record EdgeRow(String snapshotId, String relation, String fromRef, String toRef) {}

    /** structured query 结果行：行号 + 该行 {列名: 值} JSON。 */
    record StructuredRow(Integer rowIndex, String cellsJson) {}

    /** 聚合结果：value 数值列（count 恒非空；sum/min/max/avg 无匹配行时可为 null）。 */
    record AggregateRow(Double value) {}

    /**
     * structured query 过滤条件（白名单校验后传入）。{@code numeric} 决定 SQL 分支
     * （数值比较走 ::numeric 转换；文本走字典序）——由列能力扫描判定，非请求声明。
     */
    class Criterion {
        private final String field;
        private final String op;
        private final Object value;
        private final List<String> values;
        private final boolean numeric;

        public Criterion(String field, String op, Object value, List<String> values, boolean numeric) {
            this.field = field;
            this.op = op;
            this.value = value;
            this.values = values;
            this.numeric = numeric;
        }

        public String getField() { return field; }
        public String getOp() { return op; }
        public Object getValue() { return value; }
        public List<String> getValues() { return values; }
        public boolean isNumeric() { return numeric; }
    }

    // ---- ref 反查候选（授权 snapshot 集内枚举 + HMAC 匹配） -------------------------

    /** st_ 候选：结构节点 ref ∪ 结构化资产 ref。 */
    List<RefRow> selectStructureRefCandidates(
            @Param("snapshotIds") List<String> snapshotIds, @Param("limit") int limit);

    /** doc_ 候选：document 型节点的 ref（= 投影 document_ref）。 */
    List<RefRow> selectDocumentRefCandidates(
            @Param("snapshotIds") List<String> snapshotIds, @Param("limit") int limit);

    /** ev_ 候选：distinct canonical_evidence_id。 */
    List<RefRow> selectCanonicalRefCandidates(
            @Param("snapshotIds") List<String> snapshotIds, @Param("limit") int limit);

    /** 授权库的<b>全部历史</b> snapshot（含非活动；expired_ref 判定用，LIMIT 有界）。 */
    List<RefRow> selectKbSnapshotRefs(
            @Param("domain") String domain, @Param("kbIds") List<String> kbIds,
            @Param("limit") int limit);

    // ---- structure_navigate ----------------------------------------------------------

    StructureNodeRow selectNode(@Param("snapshotId") String snapshotId, @Param("ref") String ref);

    /** 直接子节点，(ordinal NULLS LAST, ref) 稳定序，LIMIT/OFFSET 参数化。 */
    List<StructureNodeRow> selectChildren(
            @Param("snapshotId") String snapshotId, @Param("parentRef") String parentRef,
            @Param("limit") int limit, @Param("offset") int offset);

    /** 递归后代（≤ maxDepth 层），BFS 序（d, ordinal, ref）稳定，LIMIT/OFFSET。 */
    List<StructureNodeRow> selectDescendants(
            @Param("snapshotId") String snapshotId, @Param("rootRef") String rootRef,
            @Param("maxDepth") int maxDepth, @Param("limit") int limit, @Param("offset") int offset);

    /** 递归祖先（父→根，≤ maxDepth 层，最近优先）。 */
    List<StructureNodeRow> selectAncestors(
            @Param("snapshotId") String snapshotId, @Param("ref") String ref,
            @Param("maxDepth") int maxDepth);

    /** 同父兄弟（previous/next 与 container 判定；有界 1000）。 */
    List<StructureNodeRow> selectSiblings(
            @Param("snapshotId") String snapshotId, @Param("parentRef") String parentRef,
            @Param("limit") int limit);

    /** 指定关系的显式边（references/footnotes 等；仅跟随显式可追溯边）。 */
    List<EdgeRow> selectEdges(
            @Param("snapshotId") String snapshotId, @Param("fromRef") String fromRef,
            @Param("relation") String relation, @Param("limit") int limit);

    // ---- readiness / inspect ---------------------------------------------------------

    int countNodesByType(@Param("snapshotId") String snapshotId, @Param("nodeType") String nodeType);

    int countEdgesByRelation(@Param("snapshotId") String snapshotId, @Param("relation") String relation);

    int countSegments(@Param("snapshotId") String snapshotId);

    /** snapshot 内表格资产清单（inspect 概览；有界）。 */
    List<TableAssetRow> selectSnapshotTableAssets(
            @Param("snapshotId") String snapshotId, @Param("limit") int limit);

    /** 表格节点所在 section 的表示 structural_context（caption；有界 1 行）。 */
    String selectStructuralContext(
            @Param("snapshotId") String snapshotId, @Param("containerRef") String containerRef);

    // ---- structured_query --------------------------------------------------------------

    TableAssetRow selectTableAssetByAssetRef(
            @Param("snapshotId") String snapshotId, @Param("assetRef") String assetRef);

    int countCells(@Param("snapshotId") String snapshotId, @Param("tableRef") String tableRef);

    /** 列能力扫描（值类型判定；is_header DESC 让表头行先进来）。 */
    List<TableCellRow> selectCellsForTyping(
            @Param("snapshotId") String snapshotId, @Param("tableRef") String tableRef,
            @Param("limit") int limit);

    /** 行查询：pivot + criteria + 稳定排序 + 分页。 */
    List<StructuredRow> selectStructuredRows(
            @Param("snapshotId") String snapshotId, @Param("tableRef") String tableRef,
            @Param("criteria") List<Criterion> criteria,
            @Param("orderField") String orderField, @Param("orderDir") String orderDir,
            @Param("numericOrder") boolean numericOrder,
            @Param("limit") int limit, @Param("offset") int offset);

    /** 符合 criteria 的行数（has_more 判定）。 */
    long countStructuredRows(
            @Param("snapshotId") String snapshotId, @Param("tableRef") String tableRef,
            @Param("criteria") List<Criterion> criteria);

    /** 聚合：op ∈ count/sum/min/max/avg（aggOp 固定分支；aggField 白名单 + 参数绑定）。 */
    AggregateRow aggregateStructuredRows(
            @Param("snapshotId") String snapshotId, @Param("tableRef") String tableRef,
            @Param("criteria") List<Criterion> criteria,
            @Param("aggOp") String aggOp, @Param("aggField") String aggField);

    // ---- get_document -------------------------------------------------------------------

    /** 文档 segment 分页（afterIndex 之后按 segment_index 升序，有界）。 */
    List<SegmentTextRow> selectSegmentsPage(
            @Param("snapshotId") String snapshotId, @Param("afterIndex") int afterIndex,
            @Param("limit") int limit);

    /** 章节 outline（title/level/parent；有界）。 */
    List<StructureNodeRow> selectSectionOutline(
            @Param("snapshotId") String snapshotId, @Param("limit") int limit);
}
