package com.coremasterkb.serving.operator.operators.retrieve;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.QueryUnderstanding;
import com.coremasterkb.serving.operator.core.*;
import com.coremasterkb.serving.retrieval.EntityGraphRetriever;

import java.util.List;

/**
 * 批次8 R0 研究隔离：实体线暂不上线，不注册正式目录（25号 §4/§11.1）。
 *
 * <p>{@code entity_graph} — ontology-graph recall. Links the query's entities to canonical ontology
 * entities, traverses the entity relation graph up to {@code maxHop} hops, and returns the
 * evidence retrieval units of the reached entities, scored by hop-distance decay × path
 * confidence. Reuses {@link EntityGraphRetriever}.
 *
 * <p>Same slot signature as {@code entity_exact}/{@code dense_vector} (understanding + scope →
 * candidates), so it drops straight into any retrieval paradigm and feeds the fusion node.
 * Domain comes from {@link ExecContext#domain()}; the DB is already domain-routed for the request.</p>
 */
public class EntityGraphOperator implements Operator {

    private static final String PARAM_SCHEMA = """
            {"type":"object","properties":{
              "topK":      {"type":"integer","minimum":1,"maximum":200,"default":20,"title":"返回数量"},
              "maxHop":    {"type":"integer","minimum":1,"maximum":3,"default":2,"title":"最大跳数"},
              "minRelConf":{"type":"number","minimum":0,"maximum":1,"default":0.5,"title":"关系置信下限"},
              "decay":     {"type":"number","minimum":0,"maximum":1,"default":0.6,"title":"跳数衰减"}
            }}""";

    private final EntityGraphRetriever retriever;

    public EntityGraphOperator(EntityGraphRetriever retriever) {
        this.retriever = retriever;
    }

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "entity_graph", "retrieve", "本体图谱检索",
                "查询实体消歧到 canonical 实体，沿本体关系多跳，经出处回证据段召回",
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
        var opts = new EntityGraphRetriever.Options(
                params.getInt("topK", 20),
                params.getInt("maxHop", 2),
                params.getDouble("minRelConf", 0.5),
                params.getDouble("decay", 0.6));
        return SlotValues.of("candidates",
                retriever.retrieve(u, scope.snapshotIds(), ctx.domain(), opts));
    }
}
