package com.coremasterkb.serving.operator;

import com.coremasterkb.serving.operator.core.Operator;
import com.coremasterkb.serving.operator.core.OperatorDef;
import com.coremasterkb.serving.operator.core.exceptions.ParadigmCompileException;
import com.coremasterkb.serving.operator.engine.ParadigmCompiler;
import com.coremasterkb.serving.operator.operators.fuse.RrfOperator;
import com.coremasterkb.serving.operator.operators.output.AssembleOperator;
import com.coremasterkb.serving.operator.operators.output.ScopeResolveOperator;
import com.coremasterkb.serving.operator.operators.query.QueryEmbedOperator;
import com.coremasterkb.serving.operator.operators.rerank.ModelRerankOperator;
import com.coremasterkb.serving.operator.operators.retrieve.DenseVectorOperator;
import com.coremasterkb.serving.operator.operators.retrieve.FtsOperator;
import com.coremasterkb.serving.operator.registry.OperatorRegistry;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.config.BeanDefinition;
import org.springframework.context.annotation.ClassPathScanningCandidateComponentProvider;
import org.springframework.core.type.filter.AnnotationTypeFilter;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Validates the official operator catalog (批次8 R0, 25 号文档 §4/§11.1): exactly the seven
 * production operators may exist — anything else must not be Spring-scanned and must not be
 * constructible into a registry. Also keeps the PRD's example paradigms (plus a full production
 * paradigm ending in {@code assemble}) compiling against the real operator definitions.
 * Operators are instantiated with null dependencies — only {@code definition()} is exercised
 * (it never touches injected deps), so no Spring/DB is needed.
 */
class OperatorCatalogTest {

    private static final ObjectMapper M = new ObjectMapper();

    /** The exact official catalog after R0 clean break (25 号 §4 生命周期总表). */
    private static final Set<String> OFFICIAL_CATALOG = Set.of(
            "scope_resolve", "query_embed", "fts", "dense_vector", "rrf", "model_rerank", "assemble");

    /** Retired in R0 (deleted) or research-isolated (entity line) — must never register. */
    private static final Set<String> RETIRED_TYPES = Set.of(
            "request_input", "query_understanding", "multi_query", "hyde",
            "graph_expand", "identity", "weighted_rrf", "llm_rerank", "score_rerank", "collect",
            "entity_exact", "entity_graph");

    private static OperatorRegistry realRegistry() {
        List<Operator> all = List.of(
                new QueryEmbedOperator(null, null), new ScopeResolveOperator(null, null),
                new DenseVectorOperator(null), new FtsOperator(null),
                new RrfOperator(), new ModelRerankOperator(null), new AssembleOperator(null));
        return new OperatorRegistry(all);
    }

    private static ParadigmCompiler compiler() {
        return new ParadigmCompiler(realRegistry());
    }

    private static com.fasterxml.jackson.databind.JsonNode json(String s) {
        try { return M.readTree(s); } catch (Exception e) { throw new RuntimeException(e); }
    }

    @Test
    void catalogIsExactlyTheOfficialSeven() {
        OperatorRegistry reg = realRegistry();
        List<OperatorDef> defs = reg.allDefinitions();
        assertEquals(OFFICIAL_CATALOG.size(), defs.size());
        assertEquals(OFFICIAL_CATALOG,
                defs.stream().map(OperatorDef::type).collect(Collectors.toSet()));
        for (OperatorDef d : defs) {
            assertNotNull(d.type());
            assertFalse(d.outputSlots().isEmpty(), d.type() + " must declare an output slot");
            assertDoesNotThrow(() -> M.readTree(d.paramSchemaJson()),
                    d.type() + " paramSchema must be valid JSON");
        }
    }

    @Test
    void retiredAndResearchIsolatedTypesAreNotInCatalog() {
        OperatorRegistry reg = realRegistry();
        for (String type : RETIRED_TYPES) {
            assertFalse(reg.contains(type), type + " must not be registered after R0");
        }
    }

    /**
     * The registry is fed by Spring constructor injection of every {@code Operator} bean, so the
     * real gate is component scanning: the operators package may carry exactly the seven official
     * {@code @Component} operator classes. This is what keeps research-isolated source (entity
     * line) out of the production catalog without deleting it.
     */
    @Test
    void operatorsPackageSpringScanMatchesOfficialCatalog() {
        ClassPathScanningCandidateComponentProvider scanner =
                new ClassPathScanningCandidateComponentProvider(false);
        scanner.addIncludeFilter(new AnnotationTypeFilter(Component.class));
        Set<String> scanned = scanner
                .findCandidateComponents("com.coremasterkb.serving.operator.operators").stream()
                .map(BeanDefinition::getBeanClassName)
                .collect(Collectors.toSet());
        Set<String> expected = Set.of(
                "com.coremasterkb.serving.operator.operators.query.QueryEmbedOperator",
                "com.coremasterkb.serving.operator.operators.output.ScopeResolveOperator",
                "com.coremasterkb.serving.operator.operators.retrieve.DenseVectorOperator",
                "com.coremasterkb.serving.operator.operators.retrieve.FtsOperator",
                "com.coremasterkb.serving.operator.operators.fuse.RrfOperator",
                "com.coremasterkb.serving.operator.operators.rerank.ModelRerankOperator",
                "com.coremasterkb.serving.operator.operators.output.AssembleOperator");
        assertEquals(expected, scanned,
                "operators 包 Spring 扫描结果必须恰好等于官方 7 算子（研究隔离类不得带 @Component）");
    }

    @Test
    void example1_embeddingOnly_compiles() {
        assertDoesNotThrow(() -> compiler().compile(json("""
            {"nodes":[
              {"nodeId":"qe","operatorType":"query_embed"},
              {"nodeId":"scope","operatorType":"scope_resolve"},
              {"nodeId":"dv","operatorType":"dense_vector","params":{"topK":20}}],
             "edges":[
              {"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"}],
             "output":{"nodeId":"dv","slot":"candidates"}}""")));
    }

    @Test
    void example2_embeddingPlusRerank_compiles() {
        assertDoesNotThrow(() -> compiler().compile(json("""
            {"nodes":[
              {"nodeId":"qe","operatorType":"query_embed"},
              {"nodeId":"scope","operatorType":"scope_resolve"},
              {"nodeId":"dv","operatorType":"dense_vector"},
              {"nodeId":"rr","operatorType":"model_rerank","params":{"topK":10}}],
             "edges":[
              {"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"},
              {"fromNode":"dv","fromSlot":"candidates","toNode":"rr","toSlot":"candidates"}],
             "output":{"nodeId":"rr","slot":"candidates"}}""")));
    }

    @Test
    void example3_multiRouteFusion_compiles() {
        assertDoesNotThrow(() -> compiler().compile(json("""
            {"nodes":[
              {"nodeId":"qe","operatorType":"query_embed"},
              {"nodeId":"scope","operatorType":"scope_resolve"},
              {"nodeId":"dv","operatorType":"dense_vector","params":{"topK":20}},
              {"nodeId":"fts","operatorType":"fts","params":{"topK":20}},
              {"nodeId":"fuse","operatorType":"rrf","params":{"k":60}}],
             "edges":[
              {"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"fts","toSlot":"scope"},
              {"fromNode":"dv","fromSlot":"candidates","toNode":"fuse","toSlot":"candidates"},
              {"fromNode":"fts","fromSlot":"candidates","toNode":"fuse","toSlot":"candidates"}],
             "output":{"nodeId":"fuse","slot":"candidates"}}""")));
    }

    @Test
    void productionParadigm_withAssemble_compiles() {
        assertDoesNotThrow(() -> compiler().compile(json("""
            {"nodes":[
              {"nodeId":"qe","operatorType":"query_embed"},
              {"nodeId":"scope","operatorType":"scope_resolve"},
              {"nodeId":"dv","operatorType":"dense_vector"},
              {"nodeId":"fts","operatorType":"fts"},
              {"nodeId":"fuse","operatorType":"rrf"},
              {"nodeId":"rr","operatorType":"model_rerank"},
              {"nodeId":"asm","operatorType":"assemble"}],
             "edges":[
              {"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"fts","toSlot":"scope"},
              {"fromNode":"dv","fromSlot":"candidates","toNode":"fuse","toSlot":"candidates"},
              {"fromNode":"fts","fromSlot":"candidates","toNode":"fuse","toSlot":"candidates"},
              {"fromNode":"fuse","fromSlot":"candidates","toNode":"rr","toSlot":"candidates"},
              {"fromNode":"rr","fromSlot":"candidates","toNode":"asm","toSlot":"candidates"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"asm","toSlot":"scope"}],
             "output":{"nodeId":"asm","slot":"contextPack"}}""")));
    }

    /**
     * The cheapest servable graph: terminates in {@code assemble} — so it is bindable — yet is a
     * pure vector paradigm with no extra pipeline stages. The {@code understanding} slot is gone
     * with {@code query_understanding} (R0), so nothing here taxes the graph with an LLM call.
     */
    @Test
    void servableParadigm_pureVector_compiles() {
        assertDoesNotThrow(() -> compiler().compile(json("""
            {"nodes":[
              {"nodeId":"qe","operatorType":"query_embed"},
              {"nodeId":"scope","operatorType":"scope_resolve"},
              {"nodeId":"dv","operatorType":"dense_vector"},
              {"nodeId":"asm","operatorType":"assemble","params":{"relationExpansion":false,"maxExpanded":0}}],
             "edges":[
              {"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"},
              {"fromNode":"dv","fromSlot":"candidates","toNode":"asm","toSlot":"candidates"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"asm","toSlot":"scope"}],
             "output":{"nodeId":"asm","slot":"contextPack"}}""")));
    }

    /** {@code scope} stays required — dropping it is what {@code ENTRY_SLOTS} deliberately prevents. */
    @Test
    void assembleWithoutScope_failsToCompile() {
        var e = assertThrows(ParadigmCompileException.class, () -> compiler().compile(json("""
            {"nodes":[
              {"nodeId":"qe","operatorType":"query_embed"},
              {"nodeId":"scope","operatorType":"scope_resolve"},
              {"nodeId":"dv","operatorType":"dense_vector"},
              {"nodeId":"asm","operatorType":"assemble"}],
             "edges":[
              {"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"},
              {"fromNode":"dv","fromSlot":"candidates","toNode":"asm","toSlot":"candidates"}],
             "output":{"nodeId":"asm","slot":"contextPack"}}""")));
        assertTrue(e.errors().stream().anyMatch(
                        err -> "missing_required_input".equals(err.kind())
                                && "asm".equals(err.nodeId())
                                && err.message().contains("scope")),
                "expected a missing_required_input error on 'asm' naming scope, got: " + e.errors());
    }
}
