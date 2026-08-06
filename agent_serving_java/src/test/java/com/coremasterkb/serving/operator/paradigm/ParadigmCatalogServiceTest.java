package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.application.KbAccessService;
import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.domainpack.DomainPoolManager;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmVersionMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The connection-orchestration contract, all mocked (no DB).
 */
@DisplayName("ParadigmCatalogService")
class ParadigmCatalogServiceTest {

    private static final String SERVABLE = """
            {"nodes":[{"nodeId":"asm","operatorType":"assemble"}],
             "output":{"nodeId":"asm","slot":"contextPack"}}""";

    private static final String SERVABLE_WITH_KBS = """
            {"nodes":[{"nodeId":"sc","operatorType":"scope_resolve",
                       "params":{"kbIds":["kb-a","kb-b"]}},
                      {"nodeId":"asm","operatorType":"assemble"}],
             "output":{"nodeId":"asm","slot":"contextPack"}}""";

    private ParadigmService paradigmService;
    private ParadigmVersionMapper versionMapper;
    private DomainPoolManager poolManager;
    private KbAccessService kbAccessService;
    private ParadigmCatalogService service;

    private final List<ParadigmEntity> published = new ArrayList<>();

    @BeforeEach
    void setUp() {
        paradigmService = mock(ParadigmService.class);
        versionMapper = mock(ParadigmVersionMapper.class);
        poolManager = mock(DomainPoolManager.class);
        kbAccessService = mock(KbAccessService.class);
        service = new ParadigmCatalogService(
                paradigmService, versionMapper, poolManager, kbAccessService);
        published.clear();
        when(paradigmService.listPublished()).thenAnswer(inv -> List.copyOf(published));
        // Default: every KB is anonymously readable. Tests that care override it.
        when(kbAccessService.authorize(anyString(), anyList(), isNull()))
                .thenAnswer(inv -> inv.getArgument(1));
    }

    @AfterEach
    void tearDown() {
        DomainContext.clear();
    }

    /** Register a published paradigm and stub its version row. */
    private ParadigmEntity give(String id, String name, String domain, boolean isDefault, String graph) {
        ParadigmEntity p = new ParadigmEntity();
        p.setId(id);
        p.setName(name);
        p.setDescription("desc of " + name);
        p.setStatus("active");
        p.setCurrentVersion(3);
        p.setBoundDomain(domain);
        p.setIsDefault(isDefault);
        published.add(p);

        if (graph != null) {
            ParadigmVersionEntity v = new ParadigmVersionEntity();
            v.setParadigmId(id);
            v.setVersion(3);
            v.setGraphJson(graph);
            when(versionMapper.selectByParadigmAndVersion(id, 3)).thenReturn(v);
        }
        return p;
    }

    private static List<String> idsOf(List<ParadigmCatalogService.Entry> entries) {
        return entries.stream().map(ParadigmCatalogService.Entry::id).toList();
    }

    // =====================================================================================
    // The orchestration contract — the reason this class is structured in phases
    // =====================================================================================

    @Nested
    @DisplayName("connection orchestration")
    class Orchestration {

        /**
         * operator_paradigm lives in the control DB and is reached through the non-routed
         * DataSource; a DomainContext still set when it is read would route it into a domain's
         * database. Every domain currently points at the same physical kb_db, so that mistake
         * returns rows instead of failing — this assertion is the only thing that would catch it.
         */
        @Test
        @DisplayName("control-DB reads run with no DomainContext")
        void controlDbReadsSeeNoDomain() {
            give("pd-1", "kb scoped", "odn", true, SERVABLE_WITH_KBS);

            AtomicReference<String> atList = new AtomicReference<>("<not-invoked>");
            AtomicReference<String> atVersion = new AtomicReference<>("<not-invoked>");
            when(paradigmService.listPublished()).thenAnswer(inv -> {
                atList.set(DomainContext.get());
                return List.copyOf(published);
            });
            when(versionMapper.selectByParadigmAndVersion(anyString(), org.mockito.ArgumentMatchers.anyInt()))
                    .thenAnswer(inv -> {
                        atVersion.set(DomainContext.get());
                        ParadigmVersionEntity v = new ParadigmVersionEntity();
                        v.setGraphJson(SERVABLE_WITH_KBS);
                        return v;
                    });

            service.build(null, null);

            assertThat(atList.get()).as("listPublished must read the control DB").isNull();
            assertThat(atVersion.get()).as("version rows live in the control DB").isNull();
        }

        @Test
        @DisplayName("the KB check runs with the domain's context set")
        void kbCheckSeesTheDomain() {
            give("pd-1", "kb scoped", "odn", true, SERVABLE_WITH_KBS);

            AtomicReference<String> atCheck = new AtomicReference<>();
            when(kbAccessService.authorize(anyString(), anyList(), isNull()))
                    .thenAnswer(inv -> {
                        atCheck.set(DomainContext.get());
                        return inv.getArgument(1);
                    });

            service.build(null, null);

            assertThat(atCheck.get()).isEqualTo("odn");
            verify(poolManager).getDataSource("odn");
        }

        @Test
        @DisplayName("DomainContext does not leak past build()")
        void contextDoesNotLeak() {
            give("pd-1", "kb scoped", "odn", true, SERVABLE_WITH_KBS);

            service.build(null, null);

            assertThat(DomainContext.get()).isNull();
        }

        @Test
        @DisplayName("one context switch per domain, not per paradigm")
        void groupsByDomain() {
            give("pd-1", "a", "odn", true, SERVABLE_WITH_KBS);
            give("pd-2", "b", "odn", false, SERVABLE_WITH_KBS);
            give("pd-3", "c", "cloud_core_network", false, SERVABLE_WITH_KBS);

            service.build(null, null);

            verify(poolManager, times(1)).getDataSource("odn");
            verify(poolManager, times(1)).getDataSource("cloud_core_network");
        }

        @Test
        @DisplayName("a graph without kbIds costs no domain round trip at all")
        void unscopedGraphSkipsTheDomainDb() {
            give("pd-1", "unscoped", "odn", true, SERVABLE);

            ParadigmCatalogService.Catalog c = service.build(null, null);

            assertThat(idsOf(c.paradigms())).containsExactly("pd-1");
            verify(poolManager, never()).getDataSource(anyString());
            verify(kbAccessService, never()).authorize(anyString(), anyList(), any());
        }
    }

}
