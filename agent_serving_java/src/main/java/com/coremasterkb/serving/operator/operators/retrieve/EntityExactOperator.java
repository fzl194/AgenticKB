package com.coremasterkb.serving.operator.operators.retrieve;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.QueryUnderstanding;
import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.domain.RetrievalQuery;
import com.coremasterkb.serving.operator.core.*;
import com.coremasterkb.serving.retrieval.EntityExactRetriever;

import java.util.List;

/**
 * 批次8 R0 研究隔离：实体线暂不上线，不注册正式目录（25号 §4/§11.1）。
 *
 * <p>{@code entity_exact} — exact entity-name match via JSONB containment. Reuses the existing
 * {@link EntityExactRetriever} (fixed high-confidence score). Takes the query understanding so it
 * can match on extracted entity names (falls back to keywords inside the retriever).</p>
 */
public class EntityExactOperator implements Operator {

    private static final String PARAM_SCHEMA = """
            {"type":"object","properties":{
              "topK":{"type":"integer","minimum":1,"maximum":200,"default":20,"title":"返回数量"}
            }}""";

    private final EntityExactRetriever retriever;

    public EntityExactOperator(EntityExactRetriever retriever) {
        this.retriever = retriever;
    }

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "entity_exact", "retrieve", "实体检索",
                "按实体名精确匹配（JSONB containment），适合命令/网元名查找",
                List.of(
                        SlotDecl.required("understanding", SlotType.QUERY_UNDERSTANDING, "查询理解(实体)"),
                        SlotDecl.required("scope", SlotType.SCOPE, "检索范围(snapshotIds)")),
                List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "检索候选")),
                PARAM_SCHEMA,
                ErrorPolicy.SKIP_WITH_EMPTY);
    }

    @Override
    public SlotValues execute(SlotValues inputs, Params params, ExecContext ctx) {
        QueryUnderstanding u = inputs.getUnderstanding("understanding");
        ActiveScope scope = inputs.getScope("scope");
        if (u == null || scope == null || scope.snapshotIds().isEmpty()) {
            return SlotValues.of("candidates", List.of());
        }
        int topK = params.getInt("topK", 20);
        RetrievalQuery rq = new RetrievalQuery(
                u.originalQuery(), u.keywords(), u.entities(), null, List.of(),
                u.intent(), u.scope(), List.of());
        List<RetrievalCandidate> candidates = retriever.retrieve(rq, scope.snapshotIds(), topK);
        return SlotValues.of("candidates", candidates);
    }
}
