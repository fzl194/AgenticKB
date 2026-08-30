package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.operator.engine.ParadigmCompiler;
import com.coremasterkb.serving.operator.engine.ParadigmGraph;
import com.coremasterkb.serving.operator.operators.fuse.RrfOperator;
import com.coremasterkb.serving.operator.operators.output.AssembleOperator;
import com.coremasterkb.serving.operator.operators.output.EvidenceHydrateOperator;
import com.coremasterkb.serving.operator.operators.output.ScopeResolveOperator;
import com.coremasterkb.serving.operator.operators.query.QueryEmbedOperator;
import com.coremasterkb.serving.operator.operators.rerank.ModelRerankOperator;
import com.coremasterkb.serving.operator.operators.retrieve.DenseVectorOperator;
import com.coremasterkb.serving.operator.operators.retrieve.FtsOperator;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmMapper;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmVersionMapper;
import com.coremasterkb.serving.operator.registry.OperatorRegistry;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.when;

/**
 * 批次8 R8（25 号 §9）：两套官方检索预置的编译与幂等 seeding。
 *
 * <ul>
 *   <li>两图必须以<b>真实</b> 7+1 算子目录编译通过（依赖/槽位契约错误在 seed 时即暴露，
 *       而不是 boot 后每次检索失败）；</li>
 *   <li>终点槽 = {@code evidenceResponse}（isServable 判定）；</li>
 *   <li>seeding 幂等：缺失建+发布、version=0 补发布、active/archived 不动；旧 id
 *       {@code system-official-default} 不复活。</li>
 * </ul>
 */
@DisplayName("OfficialParadigmSeeder (R8 两套预置)")
class OfficialParadigmSeedsTest {

    private static final ObjectMapper M = new ObjectMapper();

    private ParadigmMapper paradigmMapper;
    private ParadigmVersionMapper versionMapper;
    private ParadigmService service;

    @BeforeEach
    void setUp() {
        paradigmMapper = mock(ParadigmMapper.class);
        versionMapper = mock(ParadigmVersionMapper.class);
        OperatorRegistry registry = new OperatorRegistry(List.of(
                new QueryEmbedOperator(null, null), new ScopeResolveOperator(null, null, null),
                new DenseVectorOperator(null), new FtsOperator(null),
                new RrfOperator(), new ModelRerankOperator(null),
                new EvidenceHydrateOperator(null), new AssembleOperator(null)));
        service = new ParadigmService(paradigmMapper, versionMapper, new ParadigmCompiler(registry),
                mock(com.coremasterkb.serving.mapper.KnowledgeBaseMapper.class));
    }

    @Test
    @DisplayName("两套官方图以真实算子目录编译通过，终点 evidenceResponse")
    void bothGraphsCompileAgainstRealOperators() {
        ParadigmCompiler compiler = compilerOf(service);
        for (String graph : List.of(ParadigmService.OFFICIAL_LEXICAL_GRAPH,
                ParadigmService.OFFICIAL_HYBRID_GRAPH)) {
            ParadigmGraph compiled = compiler.compile(json(graph));
            assertThat(compiled).isNotNull();
            assertThat(json(graph).get("output").get("slot").asText())
                    .isEqualTo("evidenceResponse");
        }
    }

    @Test
    @DisplayName("评测图可把召回算子的 candidates 直接声明为输出（kb-ui 模板契约，§6.18）")
    void evalGraphDeclaresCandidatesOutputDirectly() {
        // kb-ui 模板 ③④（dense-eval/fts-eval）的形状：不再需要 collect 直通节点
        String denseEval = """
                {"schemaVersion":"1.0",
                 "nodes":[{"nodeId":"qe","operatorType":"query_embed"},
                          {"nodeId":"scope","operatorType":"scope_resolve"},
                          {"nodeId":"dv","operatorType":"dense_vector","params":{"topK":20}}],
                 "edges":[{"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
                          {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"}],
                 "output":{"nodeId":"dv","slot":"candidates"}}
                """;
        assertThat(compilerOf(service).compile(json(denseEval))).isNotNull();
    }

    @Test
    @DisplayName("混合图不含已退役算子（weighted_rrf 等 R0 清单）")
    void hybridGraphUsesOnlyCurrentCatalog() {
        String graph = ParadigmService.OFFICIAL_HYBRID_GRAPH;
        for (String retired : List.of("weighted_rrf", "collect", "query_understanding",
                "entity_exact", "llm_rerank", "graph_expand", "identity", "hyde", "multi_query",
                "score_rerank", "request_input")) {
            assertThat(graph).doesNotContain("\"operatorType\": \"" + retired + "\"");
        }
    }

    @Test
    @DisplayName("缺失 → 建+发布两套（固定 id）")
    void seedsBothWhenMissing() {
        inMemoryParadigms();
        when(versionMapper.selectMaxVersion(anyString())).thenReturn(null);

        service.ensureOfficialParadigms();

        ArgumentCaptor<ParadigmEntity> cap = ArgumentCaptor.forClass(ParadigmEntity.class);
        verify(paradigmMapper, org.mockito.Mockito.times(2)).insert(cap.capture());
        assertThat(cap.getAllValues()).extracting(ParadigmEntity::getId)
                .containsExactlyInAnyOrder(
                        ParadigmService.OFFICIAL_LEXICAL_ID, ParadigmService.OFFICIAL_DEFAULT_ID);
        // publish 走版本表（system-seeder 署名）
        verify(versionMapper, org.mockito.Mockito.times(2))
                .insert(any(ParadigmVersionEntity.class));
        assertThat(ParadigmService.OFFICIAL_DEFAULT_ID).isEqualTo("system-hybrid-retrieval");
    }

    @Test
    @DisplayName("已发布且草稿与官方图一致 → 不动（幂等，不涨版本）")
    void skipsPublishedWhenAligned() {
        ParadigmEntity lexical = entity(ParadigmService.OFFICIAL_LEXICAL_ID, 3, "active");
        lexical.setDraftGraphJson(ParadigmService.OFFICIAL_LEXICAL_GRAPH);
        ParadigmEntity hybrid = entity(ParadigmService.OFFICIAL_DEFAULT_ID, 1, "active");
        hybrid.setDraftGraphJson(ParadigmService.OFFICIAL_HYBRID_GRAPH);
        when(paradigmMapper.selectById(ParadigmService.OFFICIAL_LEXICAL_ID)).thenReturn(lexical);
        when(paradigmMapper.selectById(ParadigmService.OFFICIAL_DEFAULT_ID)).thenReturn(hybrid);

        service.ensureOfficialParadigms();

        verify(paradigmMapper, never()).insert(any(ParadigmEntity.class));
        verify(paradigmMapper, never()).updateDraft(anyString(), anyString());
        verify(versionMapper, never()).insert(any(ParadigmVersionEntity.class));
    }

    @Test
    @DisplayName("29fix R07：已发布但草稿漂移（存量旧图缺 scope→query_embed 边）→ 重发布一次")
    void republishesDriftedOfficialParadigm() {
        // 存量库典型形态：旧 hybrid 图 + 空草稿的 lexical
        ParadigmEntity hybrid = entity(ParadigmService.OFFICIAL_DEFAULT_ID, 1, "active");
        hybrid.setDraftGraphJson("{\"schemaVersion\":\"1.0\",\"nodes\":[],\"edges\":[]}");
        ParadigmEntity lexical = entity(ParadigmService.OFFICIAL_LEXICAL_ID, 2, "active");
        lexical.setDraftGraphJson(ParadigmService.OFFICIAL_LEXICAL_GRAPH);
        when(paradigmMapper.selectById(ParadigmService.OFFICIAL_LEXICAL_ID))
                .thenReturn(lexical);
        when(paradigmMapper.selectById(ParadigmService.OFFICIAL_DEFAULT_ID))
                .thenReturn(hybrid);
        // mapper 持久化语义：updateDraft 后再读返回新草稿（publish 会复读）
        doAnswer(inv -> {
            hybrid.setDraftGraphJson(inv.getArgument(1));
            return null;
        }).when(paradigmMapper).updateDraft(
                eq(ParadigmService.OFFICIAL_DEFAULT_ID), anyString());

        service.ensureOfficialParadigms();

        // 漂移者：草稿对齐官方图并发布新版本（system-preset-refresh）
        verify(paradigmMapper).updateDraft(
                eq(ParadigmService.OFFICIAL_DEFAULT_ID),
                eq(ParadigmService.OFFICIAL_HYBRID_GRAPH));
        verify(versionMapper, times(1)).insert(any(ParadigmVersionEntity.class));
        // 未漂移者不动
        verify(paradigmMapper, never()).updateDraft(
                eq(ParadigmService.OFFICIAL_LEXICAL_ID), anyString());
    }

    @Test
    @DisplayName("用户显式归档 → 不复活（运营决定）")
    void doesNotReviveArchived() {
        when(paradigmMapper.selectById(ParadigmService.OFFICIAL_LEXICAL_ID))
                .thenReturn(entity(ParadigmService.OFFICIAL_LEXICAL_ID, 2, "archived"));
        when(paradigmMapper.selectById(ParadigmService.OFFICIAL_DEFAULT_ID))
                .thenReturn(entity(ParadigmService.OFFICIAL_DEFAULT_ID, 2, "archived"));

        service.ensureOfficialParadigms();

        verify(paradigmMapper, never()).insert(any(ParadigmEntity.class));
        verify(paradigmMapper, never()).updatePublish(anyString(), org.mockito.ArgumentMatchers.anyInt(),
                anyString());
    }

    @Test
    @DisplayName("存在但从未发布（version=0）→ 补发布")
    void publishesUnpublishedDraft() {
        ParadigmEntity draft = entity(ParadigmService.OFFICIAL_LEXICAL_ID, 0, "draft");
        draft.setDraftGraphJson(ParadigmService.OFFICIAL_LEXICAL_GRAPH);
        store.put(ParadigmService.OFFICIAL_LEXICAL_ID, draft);
        inMemoryParadigms();
        when(versionMapper.selectMaxVersion(anyString())).thenReturn(null);

        service.ensureOfficialParadigms();

        // lexical（补发布）+ hybrid（缺失 → 建+发布）各一次版本插入
        verify(versionMapper, org.mockito.Mockito.times(2)).insert(any(ParadigmVersionEntity.class));
        verify(paradigmMapper).updatePublish(eq(ParadigmService.OFFICIAL_LEXICAL_ID),
                eq(1), eq("active"));
    }

    // ---- helpers ------------------------------------------------------------------------

    /** publish() 内部 getOrThrow 会回查 insert 后的行——用内存 map 模拟可回查的 mapper。 */
    private final java.util.Map<String, ParadigmEntity> store = new java.util.HashMap<>();

    private void inMemoryParadigms() {
        doAnswer(inv -> {
            ParadigmEntity e = inv.getArgument(0);
            store.put(e.getId(), e);
            return 1;
        }).when(paradigmMapper).insert(any(ParadigmEntity.class));
        doAnswer(inv -> {
            String id = inv.getArgument(0);
            ParadigmEntity e = store.get(id);
            if (e != null) {
                e.setStatus("active");
                e.setCurrentVersion(inv.getArgument(1));
            }
            return 1;
        }).when(paradigmMapper).updatePublish(anyString(), org.mockito.ArgumentMatchers.anyInt(),
                anyString());
        when(paradigmMapper.selectById(anyString()))
                .thenAnswer(inv -> store.get(inv.getArgument(0)));
    }

    private static ParadigmCompiler compilerOf(ParadigmService unused) {
        OperatorRegistry registry = new OperatorRegistry(List.of(
                new QueryEmbedOperator(null, null), new ScopeResolveOperator(null, null, null),
                new DenseVectorOperator(null), new FtsOperator(null),
                new RrfOperator(), new ModelRerankOperator(null),
                new EvidenceHydrateOperator(null), new AssembleOperator(null)));
        return new ParadigmCompiler(registry);
    }

    private static ParadigmEntity entity(String id, int version, String status) {
        ParadigmEntity e = new ParadigmEntity();
        e.setId(id);
        e.setName("n-" + id);
        e.setCurrentVersion(version);
        e.setStatus(status);
        return e;
    }

    private static com.fasterxml.jackson.databind.JsonNode json(String s) {
        try {
            return M.readTree(s);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

}
