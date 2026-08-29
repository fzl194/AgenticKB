package com.coremasterkb.serving.operator.operators.rerank;

import com.coremasterkb.serving.domain.EvidenceNeed;
import com.coremasterkb.serving.domain.QueryUnderstanding;
import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.operator.core.*;
import com.coremasterkb.serving.rerank.LlmServiceReranker;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * {@code model_rerank} — 专用 reranker 重排（批次8 R4，25 号 §6.7）。
 *
 * <p>只对融合后的 <b>Top-N</b>（参数 {@code topN}，默认 50）调用 llm_service 的专用 rerank
 * 模型（非生成式 LLM）；排序输入用候选自带的 {@code rankingText}（R2 类型化有界构造：
 * structural_context + content_text，截断 2048；alias 的 content_text 即源证据摘要）。输出
 * 只改变顺序与专用 score，不改变 canonical target/filters/source refs。</p>
 *
 * <p>失败语义（超时/服务不可用/返回数量或 identity 不合法——{@link LlmServiceReranker}
 * 返回 null）：原样保留 RRF 顺序 + degraded 留痕（ctx attribute + 日志），<b>绝不调用
 * 生成式 fallback</b>（llm_rerank 已随 R0 删除，无此路径）。旧 {@code threshold} 参数
 * 已删除——阈值过滤不属重排职责。</p>
 */
@Component
public class ModelRerankOperator implements Operator {

    private static final Logger log = LoggerFactory.getLogger(ModelRerankOperator.class);

    private static final String PARAM_SCHEMA = """
            {"type":"object","properties":{
              "topN":{"type":"integer","minimum":1,"maximum":200,"default":50,\
            "title":"送入重排的 Top-N","description":"只对融合后前 N 个候选调用 reranker"},
              "topK":{"type":"integer","minimum":1,"maximum":200,"default":10,"title":"返回数量"}
            }}""";

    private final LlmServiceReranker llmServiceReranker;

    public ModelRerankOperator(LlmServiceReranker llmServiceReranker) {
        this.llmServiceReranker = llmServiceReranker;
    }

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "model_rerank", "rerank", "模型重排",
                "对融合后 Top-N 调用专用 reranker；失败原样保留 RRF 顺序并留痕",
                List.of(
                        SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "候选"),
                        SlotDecl.required("query", SlotType.STRING, "查询文本")),
                List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "重排候选")),
                PARAM_SCHEMA,
                ErrorPolicy.FAIL_FAST);
    }

    @Override
    public SlotValues execute(SlotValues inputs, Params params, ExecContext ctx) {
        List<RetrievalCandidate> candidates = inputs.getCandidates("candidates");
        if (candidates.isEmpty()) {
            return SlotValues.of("candidates", List.of());
        }
        String query = inputs.getString("query");

        int topN = params.getInt("topN", 50);
        int topK = ctx.resolveTopK(params.getInt("topK", 10), 200);

        // 只送 Top-N（RRF 顺序的前缀）；N 之外的候选不参与重排但保留在尾部。
        List<RetrievalCandidate> workingSet = candidates.subList(0, Math.min(topN, candidates.size()));

        List<RetrievalCandidate> reranked = rerank(query, workingSet);
        if (reranked == null) {
            // 降级保序：原样保留 RRF 顺序（整段，不只 workingSet）。
            ctx.putAttribute("modelRerankDegraded", "reranker_unavailable_or_invalid_response");
            log.warn("[model_rerank] reranker failed — keeping RRF order (degraded), topN={}", topN);
            reranked = candidates;
        } else if (reranked.size() < candidates.size()) {
            // LlmServiceReranker 只重排 workingSet：把 N 之外的候选按原顺序接在尾部。
            List<RetrievalCandidate> tail =
                    candidates.subList(workingSet.size(), candidates.size());
            List<RetrievalCandidate> merged = new java.util.ArrayList<>(reranked.size() + tail.size());
            merged.addAll(reranked);
            merged.addAll(tail);
            reranked = merged;
        }

        List<RetrievalCandidate> result = reranked.stream()
                .limit(topK)
                .collect(Collectors.toList());
        ctx.putAttribute("modelRerankInputCount", workingSet.size());
        ctx.putAttribute("modelRerankOutputCount", result.size());
        return SlotValues.of("candidates", result);
    }

    private List<RetrievalCandidate> rerank(String query, List<RetrievalCandidate> workingSet) {
        if (llmServiceReranker == null) {
            return null;
        }
        QueryUnderstanding qu = new QueryUnderstanding(
                query, "general", List.of(), List.of(), Map.of(), List.of(),
                new EvidenceNeed(List.of(), List.of(), false, false), List.of(), "operator", "medium");
        return llmServiceReranker.rerank(workingSet, qu);
    }
}
