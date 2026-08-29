package com.coremasterkb.serving.operator.operators.query;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.infrastructure.EmbeddingClient;
import com.coremasterkb.serving.operator.core.*;
import com.coremasterkb.serving.operator.core.exceptions.OperatorException;
import com.coremasterkb.serving.operator.mapper.AssetRetrievalUnitV2Mapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * {@code query_embed} — 查询向量化（批次8 R2，25 号 §6.3）。
 *
 * <p>每次请求只对 query 调用<b>一次</b>当前文本 embedding 模型；不按 knowledge
 * representation 循环生成多种 query 向量。带活动 Build embedding profile 的 dimension 相容
 * 校验：从 v2 embeddings 表按 snapshot 取 distinct dimension，查询向量维度不在其中 →
 * 明确失败（OperatorException），不静默降级。</p>
 *
 * <p>{@code scope} 输入是<b>可选</b>的：未连接（如纯词法评测图）时跳过维度校验，只做嵌入。
 * 可观测：dim/latency 进 ctx attributes 与日志（model 名称由 llm_service 管理，客户端未回传
 * ——留待后续可观测波次）。</p>
 */
@Component
public class QueryEmbedOperator implements Operator {

    private static final Logger log = LoggerFactory.getLogger(QueryEmbedOperator.class);

    private static final String PARAM_SCHEMA = "{\"type\":\"object\",\"properties\":{}}";

    private final EmbeddingClient embeddingClient;
    private final AssetRetrievalUnitV2Mapper mapper;

    public QueryEmbedOperator(EmbeddingClient embeddingClient, AssetRetrievalUnitV2Mapper mapper) {
        this.embeddingClient = embeddingClient;
        this.mapper = mapper;
    }

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "query_embed", "query", "查询向量化",
                "调用嵌入服务把查询文本转成向量（每请求一次；带活动 Build 维度相容校验）",
                List.of(
                        SlotDecl.required("query", SlotType.STRING, "查询文本"),
                        SlotDecl.optional("scope", SlotType.SCOPE, "检索范围(可选：提供时做维度相容校验)")),
                List.of(SlotDecl.required("queryEmbedding", SlotType.VECTOR, "查询向量")),
                PARAM_SCHEMA,
                ErrorPolicy.FAIL_FAST);
    }

    @Override
    public SlotValues execute(SlotValues inputs, Params params, ExecContext ctx) {
        String query = inputs.getString("query");
        if (query == null || query.isBlank()) {
            throw new OperatorException("query_embed: empty query");
        }
        if (embeddingClient == null || !embeddingClient.isConfigured()) {
            throw new OperatorException("query_embed: embedding service not configured (LLM_SERVICE_URL blank)");
        }

        long start = System.nanoTime();
        float[] vec = embeddingClient.embed(query);
        long latencyMs = (System.nanoTime() - start) / 1_000_000;
        if (vec == null || vec.length == 0) {
            throw new OperatorException("query_embed: embedding service returned no vector");
        }

        // 可观测（§6.3）：dim/latency；不记 query 全文。
        ctx.putAttribute("queryEmbedDim", vec.length);
        ctx.putAttribute("queryEmbedLatencyMs", latencyMs);
        log.info("[query_embed] dim={} latencyMs={} (single embed per request)", vec.length, latencyMs);

        // 活动 Build embedding profile 维度相容校验（§6.3）：scope 提供且库内有向量数据才校验。
        ActiveScope scope = inputs.getScope("scope");
        if (scope != null && !scope.snapshotIds().isEmpty() && mapper != null) {
            List<Integer> profileDims = mapper.selectDistinctDimensions(scope.snapshotIds());
            if (!profileDims.isEmpty() && !profileDims.contains(vec.length)) {
                throw new OperatorException(
                        "query_embed: embedding dimension mismatch (query dim " + vec.length
                                + " not in active build profile dims " + profileDims
                                + ") — re-embed the build or use the lexical preset");
            }
        }

        return SlotValues.of("queryEmbedding", vec);
    }
}
