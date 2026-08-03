package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.application.KbAccessService;
import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.domainpack.DomainPoolManager;
import com.coremasterkb.serving.domainpack.DomainRegistry;
import com.coremasterkb.serving.mapper.KnowledgeBaseMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.*;

/**
 * Binding validation + the DomainContext/transaction ordering contract, all mocked (no DB).
 */
class ParadigmBindingServiceTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    /** Terminates in assemble → servable. */
    private static final String SERVABLE = """
            {"nodes":[{"nodeId":"sc","operatorType":"scope_resolve"},
                      {"nodeId":"asm","operatorType":"assemble"}],
             "edges":[],"output":{"nodeId":"asm","slot":"contextPack"}}""";

    /** Terminates in collect → evaluation-only, must not be bound. */
    private static final String COLLECT_ONLY = """
            {"nodes":[{"nodeId":"out","operatorType":"collect"}],
             "edges":[],"output":{"nodeId":"out","slot":"candidates"}}""";

    private static final String SERVABLE_WITH_KBS = """
            {"nodes":[{"nodeId":"sc","operatorType":"scope_resolve",
                       "params":{"kbIds":["kb-public","kb-private"]}},
                      {"nodeId":"asm","operatorType":"assemble"}],
             "edges":[],"output":{"nodeId":"asm","slot":"contextPack"}}""";

    private ParadigmService paradigmService;
    private DomainRegistry domainRegistry;
    private DomainPoolManager poolManager;
    private KbAccessService kbAccessService;
    private KnowledgeBaseMapper knowledgeBaseMapper;
    private ParadigmBindingService service;

    @BeforeEach
    void setUp() {
        paradigmService = mock(ParadigmService.class);
        domainRegistry = mock(DomainRegistry.class);
        poolManager = mock(DomainPoolManager.class);
        kbAccessService = mock(KbAccessService.class);
        knowledgeBaseMapper = mock(KnowledgeBaseMapper.class);
        service = new ParadigmBindingService(
                paradigmService, domainRegistry, poolManager, kbAccessService, knowledgeBaseMapper);
    }

    @AfterEach
    void tearDown() {
        DomainContext.clear();
    }

    // ---- happy path + the ordering landmine ----------------------------------------------

    @Test
    void bindsPublishedServableParadigm() {
        stubParadigm("pd-1", 3, "active", SERVABLE);

        service.bind("pd-1", "odn", true);

        verify(paradigmService).applyBinding("pd-1", "odn", true);
    }

    /**
     * The whole reason binding is split into validate-then-persist: the transaction manager sits on
     * the routing DataSource, so a DomainContext still set when applyBinding opens its transaction
     * would send control-DB writes to the domain's database. In production every domain points at
     * the same physical DB, so this would not fail — it would silently write to the wrong store.
     */
    @Test
    void domainContextIsClearWhenTheControlDbTransactionOpens() {
        stubParadigm("pd-1", 1, "active", SERVABLE_WITH_KBS);
        when(kbAccessService.authorize(eq("odn"), anyList(), isNull()))
                .thenReturn(List.of("kb-public", "kb-private"));

        AtomicReference<String> domainAtPersist = new AtomicReference<>("<not-invoked>");
        when(paradigmService.applyBinding(anyString(), anyString(), anyBoolean()))
                .thenAnswer(inv -> {
                    domainAtPersist.set(DomainContext.get());
                    return new ParadigmEntity();
                });

        service.bind("pd-1", "odn", true);

        assertNull(domainAtPersist.get(),
                "DomainContext must be cleared before the control-DB transaction opens");
        assertNull(DomainContext.get(), "DomainContext must not leak past bind()");
    }

    @Test
    void kbCheckRunsWithTheDomainContextSet() {
        stubParadigm("pd-1", 1, "active", SERVABLE_WITH_KBS);
        AtomicReference<String> domainDuringCheck = new AtomicReference<>();
        when(kbAccessService.authorize(anyString(), anyList(), isNull()))
                .thenAnswer(inv -> {
                    domainDuringCheck.set(DomainContext.get());
                    return List.of("kb-public", "kb-private");
                });

        service.bind("pd-1", "odn", false);

        assertEquals("odn", domainDuringCheck.get(),
                "the knowledge_bases read must run against the domain's pool");
        verify(poolManager).getDataSource("odn");
    }

    @Test
    void graphWithoutKbIdsSkipsTheDomainRoundTripEntirely() {
        stubParadigm("pd-1", 1, "active", SERVABLE);

        service.bind("pd-1", "odn", false);

        verifyNoInteractions(kbAccessService);
        verifyNoInteractions(poolManager);
        verify(paradigmService).applyBinding("pd-1", "odn", false);
    }

    // ---- the four validations -------------------------------------------------------------

    @Test
    void rejectsUnknownDomain() {
        stubParadigm("pd-1", 1, "active", SERVABLE);
        doThrow(new IllegalArgumentException("unknown_domain"))
                .when(domainRegistry).resolve("typo_domain");

        var ex = assertThrows(ParadigmBindingException.class,
                () -> service.bind("pd-1", "typo_domain", true));

        assertEquals("unknown_domain", ex.code());
        verify(paradigmService, never()).applyBinding(anyString(), anyString(), anyBoolean());
    }

    @Test
    void rejectsUnpublishedParadigm() {
        stubParadigm("pd-1", 0, "draft", SERVABLE);

        var ex = assertThrows(ParadigmBindingException.class,
                () -> service.bind("pd-1", "odn", true));

        assertEquals("paradigm_not_published", ex.code());
    }

    @Test
    void rejectsArchivedParadigmEvenWithAPublishedVersion() {
        stubParadigm("pd-1", 5, "archived", SERVABLE);

        var ex = assertThrows(ParadigmBindingException.class,
                () -> service.bind("pd-1", "odn", true));

        assertEquals("paradigm_not_published", ex.code());
    }

    @Test
    void rejectsCollectTerminatedParadigm() {
        stubParadigm("pd-1", 1, "active", COLLECT_ONLY);

        var ex = assertThrows(ParadigmBindingException.class,
                () -> service.bind("pd-1", "odn", true));

        assertEquals("paradigm_not_servable", ex.code());
        assertTrue(ex.getMessage().contains("assemble"), ex.getMessage());
    }

    @Test
    void rejectsParadigmReferencingNonPublicKbAndNamesTheOffender() {
        stubParadigm("pd-1", 1, "active", SERVABLE_WITH_KBS);
        when(kbAccessService.authorize(anyString(), anyList(), isNull()))
                .thenThrow(new IllegalArgumentException("kb_not_found"));
        // anonymous caller can only see the public one
        when(knowledgeBaseMapper.selectAccessibleKbIds(eq("odn"), anyList(), isNull()))
                .thenReturn(List.of("kb-public"));

        var ex = assertThrows(ParadigmBindingException.class,
                () -> service.bind("pd-1", "odn", true));

        assertEquals("paradigm_requires_identity", ex.code());
        assertEquals(List.of("kb-private"), ex.details());
        assertNull(DomainContext.get(), "context must be cleared even on the failure path");
        verify(paradigmService, never()).applyBinding(anyString(), anyString(), anyBoolean());
    }

    @Test
    void rejectsBlankDomain() {
        var ex = assertThrows(ParadigmBindingException.class, () -> service.bind("pd-1", "  ", true));
        assertEquals("domain_required", ex.code());
        verifyNoInteractions(paradigmService);
    }

    // ---- unbind ---------------------------------------------------------------------------

    @Test
    void unbindClearsDomainAndDefault() {
        when(paradigmService.getOrThrow("pd-1")).thenReturn(new ParadigmEntity());

        service.unbind("pd-1");

        verify(paradigmService).applyBinding("pd-1", null, false);
    }

    // ---- helpers --------------------------------------------------------------------------

    private void stubParadigm(String id, int currentVersion, String status, String graphJson) {
        ParadigmEntity e = new ParadigmEntity();
        e.setId(id);
        e.setName("name-" + id);
        e.setCurrentVersion(currentVersion);
        e.setStatus(status);
        when(paradigmService.getOrThrow(id)).thenReturn(e);
        when(paradigmService.applyBinding(anyString(), any(), anyBoolean()))
                .thenReturn(new ParadigmEntity());
        if (currentVersion >= 1) {
            when(paradigmService.resolveExecutableGraph(id, null)).thenReturn(parse(graphJson));
        }
    }

    private static JsonNode parse(String json) {
        try {
            return JSON.readTree(json);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
