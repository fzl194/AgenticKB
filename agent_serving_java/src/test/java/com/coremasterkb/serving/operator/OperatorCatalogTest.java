package com.coremasterkb.serving.operator;

import com.coremasterkb.serving.operator.core.Operator;
import com.coremasterkb.serving.operator.core.OperatorDef;
import com.coremasterkb.serving.operator.core.exceptions.ParadigmCompileException;
import com.coremasterkb.serving.operator.engine.ParadigmCompiler;
import com.coremasterkb.serving.operator.operators.fuse.IdentityOperator;
import com.coremasterkb.serving.operator.operators.fuse.RrfOperator;
import com.coremasterkb.serving.operator.operators.fuse.WeightedRrfOperator;
import com.coremasterkb.serving.operator.operators.output.AssembleOperator;
import com.coremasterkb.serving.operator.operators.output.CollectOperator;
import com.coremasterkb.serving.operator.operators.output.ScopeResolveOperator;
import com.coremasterkb.serving.operator.operators.query.HydeOperator;
import com.coremasterkb.serving.operator.operators.query.MultiQueryOperator;
import com.coremasterkb.serving.operator.operators.query.QueryEmbedOperator;
import com.coremasterkb.serving.operator.operators.query.QueryUnderstandingOperator;
import com.coremasterkb.serving.operator.operators.query.RequestInputOperator;
import com.coremasterkb.serving.operator.operators.rerank.LlmRerankOperator;
import com.coremasterkb.serving.operator.operators.rerank.ModelRerankOperator;
import com.coremasterkb.serving.operator.operators.rerank.ScoreRerankOperator;
import com.coremasterkb.serving.operator.operators.retrieve.DenseVectorOperator;
import com.coremasterkb.serving.operator.operators.retrieve.EntityExactOperator;
import com.coremasterkb.serving.operator.operators.retrieve.FtsOperator;
import com.coremasterkb.serving.operator.registry.OperatorRegistry;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Validates the real operator catalog: every operator's definition is well-formed and the PRD's
 * example paradigms (plus a full production paradigm ending in {@code assemble}) compile against
 * the real operator definitions. Operators are instantiated with null dependencies — only
 * {@code definition()} is exercised (it never touches injected deps), so no Spring/DB is needed.
 */
class OperatorCatalogTest {

    private static final ObjectMapper M = new ObjectMapper();

    private static OperatorRegistry realRegistry() {
        List<Operator> all = List.of(
                new QueryEmbedOperator(null), new QueryUnderstandingOperator(null, null),
                new HydeOperator(null), new MultiQueryOperator(null), new RequestInputOperator(),
                new ScopeResolveOperator(null, null),
                new DenseVectorOperator(null), new FtsOperator(null),
                new EntityExactOperator(null),
                new com.coremasterkb.serving.operator.operators.retrieve.GraphExpandOperator(null),
                new RrfOperator(), new WeightedRrfOperator(), new IdentityOperator(),
                new ModelRerankOperator(null), new LlmRerankOperator(null), new ScoreRerankOperator(),
                new CollectOperator(), new AssembleOperator(null));
        return new OperatorRegistry(all);
    }

    private static ParadigmCompiler compiler() {
        return new ParadigmCompiler(realRegistry());
    }

    private static com.fasterxml.jackson.databind.JsonNode json(String s) {
        try { return M.readTree(s); } catch (Exception e) { throw new RuntimeException(e); }
    }

    @Test
    void catalogOperatorsHaveValidSchemas() {
        OperatorRegistry reg = realRegistry();
        List<OperatorDef> defs = reg.allDefinitions();
        assertEquals(18, defs.size());
        for (OperatorDef d : defs) {
            assertNotNull(d.type());
            assertFalse(d.outputSlots().isEmpty(), d.type() + " must declare an output slot");
            assertDoesNotThrow(() -> M.readTree(d.paramSchemaJson()),
                    d.type() + " paramSchema must be valid JSON");
        }
    }

    @Test
    void example1_embeddingOnly_compiles() {
        assertDoesNotThrow(() -> compiler().compile(json("""
            {"nodes":[
              {"nodeId":"qe","operatorType":"query_embed"},
              {"nodeId":"scope","operatorType":"scope_resolve"},
              {"nodeId":"dv","operatorType":"dense_vector","params":{"textKind":"raw_text","topK":20}},
              {"nodeId":"out","operatorType":"collect","params":{"maxItems":20}}],
             "edges":[
              {"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"},
              {"fromNode":"dv","fromSlot":"candidates","toNode":"out","toSlot":"candidates"}],
             "output":{"nodeId":"out","slot":"candidates"}}""")));
    }

    @Test
    void example2_embeddingPlusRerank_compiles() {
        assertDoesNotThrow(() -> compiler().compile(json("""
            {"nodes":[
              {"nodeId":"qe","operatorType":"query_embed"},
              {"nodeId":"scope","operatorType":"scope_resolve"},
              {"nodeId":"dv","operatorType":"dense_vector"},
              {"nodeId":"rr","operatorType":"model_rerank","params":{"topK":10}},
              {"nodeId":"out","operatorType":"collect"}],
             "edges":[
              {"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"},
              {"fromNode":"dv","fromSlot":"candidates","toNode":"rr","toSlot":"candidates"},
              {"fromNode":"rr","fromSlot":"candidates","toNode":"out","toSlot":"candidates"}],
             "output":{"nodeId":"out","slot":"candidates"}}""")));
    }

    @Test
    void example3_multiRouteFusion_compiles() {
        assertDoesNotThrow(() -> compiler().compile(json("""
            {"nodes":[
              {"nodeId":"qe","operatorType":"query_embed"},
              {"nodeId":"scope","operatorType":"scope_resolve"},
              {"nodeId":"dv","operatorType":"dense_vector","params":{"topK":20}},
              {"nodeId":"fts","operatorType":"fts","params":{"topK":20}},
              {"nodeId":"fuse","operatorType":"weighted_rrf","params":{"k":60,"weights":{"dense_vector":1.2,"lexical_bm25":1.0}}},
              {"nodeId":"out","operatorType":"collect","params":{"maxItems":20}}],
             "edges":[
              {"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"fts","toSlot":"scope"},
              {"fromNode":"dv","fromSlot":"candidates","toNode":"fuse","toSlot":"candidates"},
              {"fromNode":"fts","fromSlot":"candidates","toNode":"fuse","toSlot":"candidates"},
              {"fromNode":"fuse","fromSlot":"candidates","toNode":"out","toSlot":"candidates"}],
             "output":{"nodeId":"out","slot":"candidates"}}""")));
    }

    @Test
    void productionParadigm_withAssemble_compiles() {
        assertDoesNotThrow(() -> compiler().compile(json("""
            {"nodes":[
              {"nodeId":"qu","operatorType":"query_understanding"},
              {"nodeId":"qe","operatorType":"query_embed"},
              {"nodeId":"scope","operatorType":"scope_resolve"},
              {"nodeId":"dv","operatorType":"dense_vector"},
              {"nodeId":"ee","operatorType":"entity_exact"},
              {"nodeId":"fuse","operatorType":"weighted_rrf"},
              {"nodeId":"rr","operatorType":"model_rerank"},
              {"nodeId":"asm","operatorType":"assemble"}],
             "edges":[
              {"fromNode":"qe","fromSlot":"queryEmbedding","toNode":"dv","toSlot":"queryEmbedding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"dv","toSlot":"scope"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"ee","toSlot":"scope"},
              {"fromNode":"qu","fromSlot":"understanding","toNode":"ee","toSlot":"understanding"},
              {"fromNode":"dv","fromSlot":"candidates","toNode":"fuse","toSlot":"candidates"},
              {"fromNode":"ee","fromSlot":"candidates","toNode":"fuse","toSlot":"candidates"},
              {"fromNode":"fuse","fromSlot":"candidates","toNode":"rr","toSlot":"candidates"},
              {"fromNode":"rr","fromSlot":"candidates","toNode":"asm","toSlot":"candidates"},
              {"fromNode":"qu","fromSlot":"understanding","toNode":"asm","toSlot":"understanding"},
              {"fromNode":"scope","fromSlot":"scope","toNode":"asm","toSlot":"scope"}],
             "output":{"nodeId":"asm","slot":"contextPack"}}""")));
    }

    /**
     * The cheapest servable graph: terminates in {@code assemble} — so it is bindable — yet carries
     * no {@code query_understanding} node. That operator is the only producer of the
     * {@code understanding} slot and it is an LLM call, so a required slot here would have taxed
     * every pure-vector paradigm with a roundtrip it never reads.
     */
    @Test
    void servableParadigm_withoutQueryUnderstanding_compiles() {
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
