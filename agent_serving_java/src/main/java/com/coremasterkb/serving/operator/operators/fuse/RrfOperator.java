package com.coremasterkb.serving.operator.operators.fuse;

import com.coremasterkb.serving.operator.core.*;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * {@code rrf} — 唯一融合算子（批次8 R3，25 号 §6.6；吸收已退役的 weighted_rrf 权重能力）。
 *
 * <p>{@code score(canonical) = Σ channel_weight[channel_id] / (k + rank_in_channel)}。channel_id
 * 由上游算子固定声明（fts/dense），不从任意 metadata 猜；输入按 canonical_evidence_id 对齐
 * （同 canonical 只占一个融合位）；equal score 稳定 tie-break（最佳通道名次 → canonical key）。
 * 不做 DB 查询、文本相似度去重或阈值过滤。</p>
 */
@Component
public class RrfOperator implements Operator {

    private static final String PARAM_SCHEMA = """
            {"type":"object","properties":{
              "k":{"type":"integer","minimum":1,"maximum":1000,"default":60,"title":"RRF k"},
              "channelWeights":{"type":"object","title":"通道权重",\
            "description":"{channelId: weight}，缺省全 1.0（如 {\\"fts\\":1.2,\\"dense\\":0.8}）",\
            "additionalProperties":{"type":"number","minimum":0}}
            }}""";

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "rrf", "fuse", "RRF 融合",
                "倒数排名融合：按 canonical evidence 对齐，Σ weight/(k+rank)，缺省权重 1.0",
                List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST_MULTI, "多路候选")),
                List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "融合候选")),
                PARAM_SCHEMA,
                ErrorPolicy.FAIL_FAST);
    }

    @Override
    public SlotValues execute(SlotValues inputs, Params params, ExecContext ctx) {
        var merged = inputs.getCandidates("candidates");
        int k = params.getInt("k", 60);
        var channelWeights = params.getDoubleMap("channelWeights");
        return SlotValues.of("candidates", RrfSupport.fuse(merged, k, channelWeights));
    }
}
