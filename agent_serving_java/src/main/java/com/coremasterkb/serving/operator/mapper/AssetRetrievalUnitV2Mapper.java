package com.coremasterkb.serving.operator.mapper;

import com.coremasterkb.serving.mapper.result.UnitV2Row;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 批次8 R2 召回 mapper：{@code asset_retrieval_units_v2} / {@code asset_retrieval_embeddings_v2}
 * （DDL 真相源：mining {@code retrieval_projection/schema.py}，Java 侧只消费不建表）。
 *
 * <p>取代旧 {@code OperatorEmbeddingMapper} 的 {@code unit_type/text_kind} 映射：
 * fts 只检索 {@code lexical_eligible = TRUE}，dense 只检索 {@code dense_eligible = TRUE}
 * 且维度与查询向量一致；scope（snapshot_ids）与显式 hard filters（facets JSONB containment /
 * representation_type / content_type / target_ref）全部在 Top-K 之前下推。通道内 canonical
 * 聚合由 {@code ChannelAggregator} 在 Java 侧完成（窗口有界），不在 SQL 里做无界分组。</p>
 */
public interface AssetRetrievalUnitV2Mapper {

    /**
     * fts 通道：tsvector 全文检索（'simple' 配置 + 查询侧 jieba 预分词契约）。
     *
     * @param lexicalQuery        分词后空格连接的 token 串（plainto_tsquery 输入；空白调用方应跳过）
     * @param snapshotIds         in-scope snapshot ids（Top-K 前下推）
     * @param documentJsonParams  facets_json @> 参数化 JSONB（{"document":"<ref>"}，OR 语义）
     * @param representationTypes representation_type IN (...)（evidence_types；空 = 不过滤）
     * @param contentTypes        content_type IN (...)（asset_types；空 = 不过滤）
     * @param targetRefs          target_ref IN (...)（section_refs；空 = 不过滤）
     * @param limit               有界召回窗口（Top-K 前于 canonical 聚合的多表示冗余预留）
     */
    List<UnitV2Row> searchFtsV2(
            @Param("lexicalQuery") String lexicalQuery,
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("documentJsonParams") List<String> documentJsonParams,
            @Param("representationTypes") List<String> representationTypes,
            @Param("contentTypes") List<String> contentTypes,
            @Param("targetRefs") List<String> targetRefs,
            @Param("limit") int limit);

    /**
     * dense 通道：pgvector 余弦检索（embeddings_v2 ⋈ units_v2，dense_eligible + 维度一致）。
     *
     * @param queryVector pgvector literal，e.g. {@code [0.1,0.2,...]}
     * @param dim         查询向量维度（embedding 与查询向量维度必须一致）
     * @param snapshotIds in-scope snapshot ids（在 embeddings_v2.snapshot_id 上下推）
     * @param documentJsonParams / representationTypes / contentTypes / targetRefs 同 {@link #searchFtsV2}
     * @param limit       有界召回窗口
     */
    List<UnitV2Row> searchDenseV2(
            @Param("queryVector") String queryVector,
            @Param("dim") int dim,
            @Param("snapshotIds") List<String> snapshotIds,
            @Param("documentJsonParams") List<String> documentJsonParams,
            @Param("representationTypes") List<String> representationTypes,
            @Param("contentTypes") List<String> contentTypes,
            @Param("targetRefs") List<String> targetRefs,
            @Param("limit") int limit);

    /**
     * 活动 Build 的 embedding profile 维度集（R2 query_embed 相容校验 / dense capability 诊断）。
     * 空 = 该 scope 无任何向量数据。
     */
    List<Integer> selectDistinctDimensions(@Param("snapshotIds") List<String> snapshotIds);
}
