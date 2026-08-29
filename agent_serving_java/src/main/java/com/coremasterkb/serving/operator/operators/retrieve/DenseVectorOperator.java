package com.coremasterkb.serving.operator.operators.retrieve;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import com.coremasterkb.serving.operator.core.*;
import com.coremasterkb.serving.operator.mapper.AssetRetrievalUnitV2Mapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * {@code dense_vector} — pgvector 余弦召回（批次8 R2，25 号 §6.5）。
 *
 * <p>切到 v2 资产表（{@code asset_retrieval_embeddings_v2} ⋈ {@code asset_retrieval_units_v2}）：
 * 旧 {@code textKind=raw_text|question|both} 参数与 {@code unit_type} 映射已删除——统一检索
 * 活动 Build 中 {@code dense_eligible=TRUE}、维度与查询向量一致的 embeddings；representation
 * type / content type / document / section 等全部是确定性 filter（{@link ScopeFilterPushdown}），
 * 在 Top-K 之前下推。通道内 canonical 聚合同 fts（{@link ChannelAggregator}），alias 不重复占位。</p>
 *
 * <p>无向量数据（scope 内 embeddings 表为空）不是空结果：返回空候选 + capability diagnostic
 * 留痕（ctx attribute + 日志），关键词预置不调用本算子。</p>
 */
@Component
public class DenseVectorOperator implements Operator {

    private static final Logger log = LoggerFactory.getLogger(DenseVectorOperator.class);

    /** §5.1 稳定通道标识：rrf channel weights 以此为键。 */
    public static final String CHANNEL_ID = "dense";

    static final int RECALL_MULTIPLIER = 5;
    static final int RECALL_HARD_CAP = 1000;

    private static final String PARAM_SCHEMA = """
            {"type":"object","properties":{
              "topK":{"type":"integer","minimum":1,"maximum":200,"default":20,"title":"返回数量"}
            }}""";

    private final AssetRetrievalUnitV2Mapper mapper;

    public DenseVectorOperator(AssetRetrievalUnitV2Mapper mapper) {
        this.mapper = mapper;
    }

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "dense_vector", "retrieve", "向量检索",
                "v2 资产表 pgvector 余弦检索（dense_eligible + 维度一致 + canonical 聚合，filters Top-K 前下推）",
                List.of(
                        SlotDecl.required("queryEmbedding", SlotType.VECTOR, "查询向量"),
                        SlotDecl.required("scope", SlotType.SCOPE, "检索范围(snapshotIds+hardFilters)")),
                List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "检索候选")),
                PARAM_SCHEMA,
                ErrorPolicy.SKIP_WITH_EMPTY);
    }

    @Override
    public SlotValues execute(SlotValues inputs, Params params, ExecContext ctx) {
        float[] vec = inputs.getVector("queryEmbedding");
        ActiveScope scope = inputs.getScope("scope");
        if (vec == null || vec.length == 0 || scope == null || scope.snapshotIds().isEmpty()) {
            return SlotValues.of("candidates", List.of());
        }
        int topK = ctx.resolveTopK(params.getInt("topK", 20), 200);

        ScopeFilterPushdown pushdown = ScopeFilterPushdown.from(scope);
        int recall = Math.min(RECALL_HARD_CAP, topK * RECALL_MULTIPLIER);

        List<UnitV2Row> rows = mapper.searchDenseV2(
                formatVector(vec), vec.length, scope.snapshotIds(),
                pushdown.documentJsonParams(), pushdown.representationTypes(),
                pushdown.contentTypes(), pushdown.targetRefs(), recall);

        if (rows.isEmpty() && mapper != null) {
            // 空候选的能力性留痕：区分"无向量数据"（capability 缺失）与"正常无命中"。
            List<Integer> dims = mapper.selectDistinctDimensions(scope.snapshotIds());
            if (dims.isEmpty()) {
                ctx.putAttribute("denseVectorDegraded", "no_embeddings_in_scope");
                log.warn("[dense_vector] no embeddings in scope (capability degraded) snapshots={}",
                        scope.snapshotIds().size());
            } else if (!dims.contains(vec.length)) {
                ctx.putAttribute("denseVectorDegraded", "dimension_mismatch");
                log.warn("[dense_vector] query dim {} not in scope profile dims {} (capability degraded)",
                        vec.length, dims);
            }
        }

        List<RetrievalCandidate> candidates = ChannelAggregator.aggregate(rows, CHANNEL_ID, topK);
        ctx.putAttribute("denseRowCount", rows.size());
        ctx.putAttribute("denseCanonicalCount", candidates.size());
        return SlotValues.of("candidates", candidates);
    }

    private static String formatVector(float[] vec) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < vec.length; i++) {
            if (i > 0) sb.append(',');
            sb.append(vec[i]);
        }
        return sb.append(']').toString();
    }
}
