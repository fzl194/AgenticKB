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
 * {@code fts} — 全文词法召回（批次8 R2，25 号 §6.4）。
 *
 * <p>切到 {@code asset_retrieval_units_v2}：只检索 {@code lexical_eligible=TRUE} 的表示；
 * 查询侧经 {@link QueryTokenizer}（jieba-analysis SEARCH 模式单例）分词后空格 join，交给
 * {@code plainto_tsquery('simple', ?)}——与索引侧 Python jieba 预分词
 * （{@code tokenizer_version=jieba-default-1}）同源同族，两侧版本进日志可观测。</p>
 *
 * <p>scope（snapshot_ids）与显式 hard filters（facets JSONB containment / 类型 / target_ref，
 * 见 {@link ScopeFilterPushdown}）在 Top-K 之前下推。通道内按 canonical_evidence_id 聚合
 * （{@link ChannelAggregator}）：同 canonical 多表示命中只保留 rank 最佳者，候选保留命中
 * representation id 列表。<b>空结果是正常结果</b>；存储/权限错误按异常向上传播。</p>
 */
@Component
public class FtsOperator implements Operator {

    private static final Logger log = LoggerFactory.getLogger(FtsOperator.class);

    /** §5.1 稳定通道标识：rrf channel weights 以此为键。 */
    public static final String CHANNEL_ID = "fts";

    /** canonical 聚合前的多表示冗余窗口（Top-K 前的行数上限，alias 不撑爆窗口）。 */
    static final int RECALL_MULTIPLIER = 5;
    static final int RECALL_HARD_CAP = 1000;

    private static final String PARAM_SCHEMA = """
            {"type":"object","properties":{
              "topK":{"type":"integer","minimum":1,"maximum":200,"default":20,"title":"返回数量"}
            }}""";

    private final AssetRetrievalUnitV2Mapper mapper;

    public FtsOperator(AssetRetrievalUnitV2Mapper mapper) {
        this.mapper = mapper;
    }

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "fts", "retrieve", "全文检索",
                "v2 资产表全文检索（lexical_eligible + jieba 同源分词 + canonical 聚合，filters Top-K 前下推）",
                List.of(
                        SlotDecl.required("query", SlotType.STRING, "查询文本"),
                        SlotDecl.required("scope", SlotType.SCOPE, "检索范围(snapshotIds+hardFilters)")),
                List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "检索候选")),
                PARAM_SCHEMA,
                ErrorPolicy.SKIP_WITH_EMPTY);
    }

    @Override
    public SlotValues execute(SlotValues inputs, Params params, ExecContext ctx) {
        String query = inputs.getString("query");
        ActiveScope scope = inputs.getScope("scope");
        if (query == null || query.isBlank() || scope == null || scope.snapshotIds().isEmpty()) {
            return SlotValues.of("candidates", List.of());
        }
        int topK = ctx.resolveTopK(params.getInt("topK", 20), 200);

        // 查询侧同源分词 → plainto_tsquery('simple', ?)；无 token = 正常空结果。
        String lexicalQuery = QueryTokenizer.toLexicalQuery(query);
        if (lexicalQuery.isEmpty()) {
            ctx.putAttribute("ftsEmptyReason", "query_tokenized_to_empty");
            return SlotValues.of("candidates", List.of());
        }
        log.debug("[fts] index_tokenizer={} query_tokenizer={} tokens_as_query_done",
                QueryTokenizer.INDEX_TOKENIZER_VERSION, QueryTokenizer.QUERY_TOKENIZER_VERSION);

        ScopeFilterPushdown pushdown = ScopeFilterPushdown.from(scope);
        int recall = Math.min(RECALL_HARD_CAP, topK * RECALL_MULTIPLIER);

        List<UnitV2Row> rows = mapper.searchFtsV2(
                lexicalQuery,
                scope.snapshotIds(),
                pushdown.documentJsonParams(),
                pushdown.representationTypes(),
                pushdown.contentTypes(),
                pushdown.targetRefs(),
                recall);

        List<RetrievalCandidate> candidates = ChannelAggregator.aggregate(rows, CHANNEL_ID, topK);
        ctx.putAttribute("ftsRowCount", rows.size());
        ctx.putAttribute("ftsCanonicalCount", candidates.size());
        return SlotValues.of("candidates", candidates);
    }
}
