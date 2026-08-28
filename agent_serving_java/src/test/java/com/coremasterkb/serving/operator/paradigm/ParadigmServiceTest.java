package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.operator.core.*;
import com.coremasterkb.serving.operator.core.exceptions.ParadigmCompileException;
import com.coremasterkb.serving.operator.engine.ParadigmCompiler;
import com.coremasterkb.serving.operator.operators.output.CollectOperator;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmMapper;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmVersionMapper;
import com.coremasterkb.serving.operator.registry.OperatorRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

/**
 * Unit tests for paradigm lifecycle (publish/version/resolve) with mocked mappers and a real
 * compiler — no DB. Verifies compile-before-persist and version monotonicity.
 */
class ParadigmServiceTest {

    private static final String VALID_GRAPH = """
            {"nodes":[{"nodeId":"s","operatorType":"seed"},{"nodeId":"out","operatorType":"collect"}],
             "edges":[{"fromNode":"s","fromSlot":"candidates","toNode":"out","toSlot":"candidates"}],
             "output":{"nodeId":"out","slot":"candidates"}}""";

    private static final String INVALID_GRAPH = """
            {"nodes":[{"nodeId":"x","operatorType":"does_not_exist"}],"edges":[],
             "output":{"nodeId":"x","slot":"candidates"}}""";

    private ParadigmMapper paradigmMapper;
    private ParadigmVersionMapper versionMapper;
    private ParadigmService service;

    @BeforeEach
    void setUp() {
        paradigmMapper = mock(ParadigmMapper.class);
        versionMapper = mock(ParadigmVersionMapper.class);
        // dependency-free operators sufficient to compile VALID_GRAPH
        Operator seed = new Operator() {
            public OperatorDef definition() {
                return new OperatorDef("seed", "retrieve", "seed", "", List.of(),
                        List.of(SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "")),
                        "{}", ErrorPolicy.FAIL_FAST);
            }
            public SlotValues execute(SlotValues in, Params p, ExecContext c) {
                return SlotValues.of("candidates", List.of());
            }
        };
        OperatorRegistry registry = new OperatorRegistry(List.of(seed, new CollectOperator()));
        ParadigmCompiler compiler = new ParadigmCompiler(registry);
        service = new ParadigmService(paradigmMapper, versionMapper, compiler,
                mock(com.coremasterkb.serving.mapper.KnowledgeBaseMapper.class));
    }

    @Test
    void publishCompilesAndCreatesNextVersion() {
        ParadigmEntity p = entity("pd-1", VALID_GRAPH, 2);
        when(paradigmMapper.selectById("pd-1")).thenReturn(p);
        when(versionMapper.selectMaxVersion("pd-1")).thenReturn(2);

        ParadigmVersionEntity v = service.publish("pd-1", "tester");

        assertEquals(3, v.getVersion());
        assertEquals(VALID_GRAPH, v.getGraphJson());

        ArgumentCaptor<ParadigmVersionEntity> cap = ArgumentCaptor.forClass(ParadigmVersionEntity.class);
        verify(versionMapper).insert(cap.capture());
        assertEquals(3, cap.getValue().getVersion());
        assertEquals("pd-1", cap.getValue().getParadigmId());
        verify(paradigmMapper).updatePublish("pd-1", 3, "active");
    }

    @Test
    void publishAfterRollbackUsesMaxVersionPlusOne() {
        // after rollback current_version=1 but max published version is 3 → next must be 4, not 2
        ParadigmEntity p = entity("pd-r", VALID_GRAPH, 1);
        when(paradigmMapper.selectById("pd-r")).thenReturn(p);
        when(versionMapper.selectMaxVersion("pd-r")).thenReturn(3);

        ParadigmVersionEntity v = service.publish("pd-r", "tester");

        assertEquals(4, v.getVersion());
        verify(paradigmMapper).updatePublish("pd-r", 4, "active");
    }

    @Test
    void publishRejectsInvalidDraftBeforePersisting() {
        ParadigmEntity p = entity("pd-2", INVALID_GRAPH, 0);
        when(paradigmMapper.selectById("pd-2")).thenReturn(p);

        assertThrows(ParadigmCompileException.class, () -> service.publish("pd-2", "tester"));
        verify(versionMapper, never()).insert(any());
        verify(paradigmMapper, never()).updatePublish(anyString(), anyInt(), anyString());
    }

    @Test
    void resolveExecutableGraphUsesCurrentVersionByDefault() {
        ParadigmEntity p = entity("pd-3", VALID_GRAPH, 4);
        when(paradigmMapper.selectById("pd-3")).thenReturn(p);
        ParadigmVersionEntity v = versionEntity("pd-3", 4, VALID_GRAPH);
        when(versionMapper.selectByParadigmAndVersion("pd-3", 4)).thenReturn(v);

        var graph = service.resolveExecutableGraph("pd-3", null);
        assertEquals("out", graph.get("output").get("nodeId").asText());
        verify(versionMapper).selectByParadigmAndVersion("pd-3", 4);
    }

    @Test
    void resolveExecutableGraphThrowsWhenUnpublished() {
        ParadigmEntity p = entity("pd-4", VALID_GRAPH, 0);
        when(paradigmMapper.selectById("pd-4")).thenReturn(p);
        assertThrows(ParadigmNotFoundException.class, () -> service.resolveExecutableGraph("pd-4", null));
    }

    /**
     * uq_paradigm_domain_default is a PARTIAL unique index, so claiming a domain's default slot
     * while the previous holder still has is_default=true raises 23505. The clear must therefore
     * come first — this is the "A is default → make B default" case that a single-paradigm test
     * would never catch.
     */
    @Test
    void claimingDefaultClearsThePreviousHolderFirst() {
        when(paradigmMapper.selectById("pd-b")).thenReturn(entity("pd-b", VALID_GRAPH, 1));

        service.applyBinding("pd-b", "odn", true);

        InOrder order = inOrder(paradigmMapper);
        order.verify(paradigmMapper).clearDefaultForDomain("odn");
        order.verify(paradigmMapper).updateBinding("pd-b", "odn", true);
    }

    @Test
    void bindingWithoutDefaultLeavesTheExistingDefaultAlone() {
        when(paradigmMapper.selectById("pd-c")).thenReturn(entity("pd-c", VALID_GRAPH, 1));

        service.applyBinding("pd-c", "odn", false);

        verify(paradigmMapper, never()).clearDefaultForDomain(anyString());
        verify(paradigmMapper).updateBinding("pd-c", "odn", false);
    }

    @Test
    void unbindingNeverTouchesTheDomainDefault() {
        when(paradigmMapper.selectById("pd-d")).thenReturn(entity("pd-d", VALID_GRAPH, 1));

        service.applyBinding("pd-d", null, false);

        verify(paradigmMapper, never()).clearDefaultForDomain(anyString());
        verify(paradigmMapper).updateBinding("pd-d", null, false);
    }

    @Test
    void getOrThrowThrowsForMissingId() {
        when(paradigmMapper.selectById("nope")).thenReturn(null);
        assertThrows(ParadigmNotFoundException.class, () -> service.getOrThrow("nope"));
    }

    // ---- helpers ----

    private static ParadigmEntity entity(String id, String draft, int currentVersion) {
        ParadigmEntity e = new ParadigmEntity();
        e.setId(id);
        e.setName("name-" + id);
        e.setDraftGraphJson(draft);
        e.setCurrentVersion(currentVersion);
        e.setStatus("draft");
        return e;
    }

    private static ParadigmVersionEntity versionEntity(String pid, int version, String graph) {
        ParadigmVersionEntity v = new ParadigmVersionEntity();
        v.setId("pdv-" + version);
        v.setParadigmId(pid);
        v.setVersion(version);
        v.setGraphJson(graph);
        return v;
    }
}
