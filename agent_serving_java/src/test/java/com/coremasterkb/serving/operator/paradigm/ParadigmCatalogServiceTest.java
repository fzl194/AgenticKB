package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.application.KbAccessService;
import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.domainpack.DomainPoolManager;
import com.coremasterkb.serving.mapper.KnowledgeBaseMapper;
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
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Catalog visibility rules + the connection-orchestration contract, all mocked (no DB).
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

    private static final String COLLECT_ONLY = """
            {"nodes":[{"nodeId":"out","operatorType":"collect"}],
             "output":{"nodeId":"out","slot":"candidates"}}""";

    private ParadigmService paradigmService;
    private ParadigmVersionMapper versionMapper;
    private DomainPoolManager poolManager;
    private KbAccessService kbAccessService;
    private KnowledgeBaseMapper knowledgeBaseMapper;
    private ParadigmCatalogService service;

    private final List<ParadigmEntity> published = new ArrayList<>();

    @BeforeEach
    void setUp() {
        paradigmService = mock(ParadigmService.class);
        versionMapper = mock(ParadigmVersionMapper.class);
        poolManager = mock(DomainPoolManager.class);
        kbAccessService = mock(KbAccessService.class);
        knowledgeBaseMapper = mock(KnowledgeBaseMapper.class);
        service = new ParadigmCatalogService(
                paradigmService, versionMapper, poolManager, kbAccessService, knowledgeBaseMapper);
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

    private static ParadigmCatalogService.Hidden hiddenOf(
            ParadigmCatalogService.Catalog c, String id) {
        return c.hidden().stream().filter(h -> h.id().equals(id)).findFirst().orElseThrow();
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

    // =====================================================================================
    // Visibility rules
    // =====================================================================================

    @Nested
    @DisplayName("visibility")
    class Visibility {

        @Test
        @DisplayName("published + servable + anonymously readable → visible")
        void visibleWhenAllThreeHold() {
            give("pd-1", "ODN 拓扑排障", "odn", true, SERVABLE_WITH_KBS);

            ParadigmCatalogService.Catalog c = service.build(null, null);

            assertThat(c.hidden()).isEmpty();
            assertThat(c.paradigms()).singleElement().satisfies(e -> {
                assertThat(e.id()).isEqualTo("pd-1");
                assertThat(e.name()).isEqualTo("ODN 拓扑排障");
                assertThat(e.description()).isEqualTo("desc of ODN 拓扑排障");
                assertThat(e.domain()).isEqualTo("odn");
                assertThat(e.version()).isEqualTo(3);
                assertThat(e.isDomainDefault()).isTrue();
            });
        }

        @Test
        @DisplayName("collect terminus → hidden(not_servable)")
        void collectIsHidden() {
            give("pd-1", "评测基线", "odn", false, COLLECT_ONLY);

            ParadigmCatalogService.Catalog c = service.build(null, null);

            assertThat(c.paradigms()).isEmpty();
            assertThat(hiddenOf(c, "pd-1").reason())
                    .isEqualTo(ParadigmCatalogService.NOT_SERVABLE);
        }

        @Test
        @DisplayName("kbIds an anonymous caller cannot read → hidden(kb_not_anonymously_readable)")
        void unreadableKbsAreHidden() {
            give("pd-1", "内部资料", "odn", false, SERVABLE_WITH_KBS);
            when(kbAccessService.authorize(eq("odn"), anyList(), isNull()))
                    .thenThrow(new IllegalArgumentException("kb_not_found"));
            when(knowledgeBaseMapper.selectAccessibleKbIds(eq("odn"), anyList(), isNull()))
                    .thenReturn(List.of("kb-a"));

            ParadigmCatalogService.Catalog c = service.build(null, null);

            assertThat(c.paradigms()).isEmpty();
            assertThat(hiddenOf(c, "pd-1").reason())
                    .isEqualTo(ParadigmCatalogService.KB_NOT_READABLE);
        }

        @Test
        @DisplayName("kbIds but no bound domain → hidden(unbound_kb_scope), unverifiable")
        void unboundKbScopeIsHidden() {
            give("pd-1", "无绑定", null, false, SERVABLE_WITH_KBS);

            ParadigmCatalogService.Catalog c = service.build(null, null);

            assertThat(hiddenOf(c, "pd-1").reason())
                    .isEqualTo(ParadigmCatalogService.UNBOUND_KB_SCOPE);
            verify(poolManager, never()).getDataSource(anyString());
        }

        @Test
        @DisplayName("no kbIds and no binding → visible with a null domain (caller supplies it)")
        void unscopedUnboundIsDomainAgnostic() {
            give("pd-1", "通用", null, false, SERVABLE);

            ParadigmCatalogService.Catalog c = service.build(null, null);

            assertThat(c.paradigms()).singleElement()
                    .extracting(ParadigmCatalogService.Entry::domain).isNull();
        }

        @Test
        @DisplayName("current_version pointing at a missing row → hidden(version_missing), not a 500")
        void danglingVersionIsHiddenNotFatal() {
            give("pd-1", "坏行", "odn", false, null);   // no version row stubbed
            give("pd-2", "好行", "odn", false, SERVABLE);

            ParadigmCatalogService.Catalog c = service.build(null, null);

            assertThat(hiddenOf(c, "pd-1").reason())
                    .isEqualTo(ParadigmCatalogService.VERSION_MISSING);
            assertThat(idsOf(c.paradigms())).containsExactly("pd-2");
        }
    }

    // =====================================================================================
    // Degradation
    // =====================================================================================

    @Nested
    @DisplayName("degradation")
    class Degradation {

        /**
         * One domain's database being down must not blank out the other domains' paradigms — the
         * same reasoning that keeps the MCP client's tool set when a catalog fetch fails.
         */
        @Test
        @DisplayName("an unreachable domain hides only its own paradigms")
        void unreachableDomainIsConfined() {
            give("pd-1", "odn one", "odn", false, SERVABLE_WITH_KBS);
            give("pd-2", "ccn one", "cloud_core_network", false, SERVABLE_WITH_KBS);
            when(poolManager.getDataSource("odn"))
                    .thenThrow(new IllegalStateException("domain_database_unavailable"));

            ParadigmCatalogService.Catalog c = service.build(null, null);

            assertThat(hiddenOf(c, "pd-1").reason())
                    .isEqualTo(ParadigmCatalogService.DOMAIN_UNAVAILABLE);
            assertThat(idsOf(c.paradigms())).containsExactly("pd-2");
            assertThat(DomainContext.get()).as("context must not leak from the failed group").isNull();
        }

        @Test
        @DisplayName("a mapper blowing up mid-group hides that group, context still cleared")
        void mapperFailureIsConfined() {
            give("pd-1", "odn one", "odn", false, SERVABLE_WITH_KBS);
            when(kbAccessService.authorize(eq("odn"), anyList(), isNull()))
                    .thenThrow(new RuntimeException("connection reset"));

            ParadigmCatalogService.Catalog c = service.build(null, null);

            assertThat(hiddenOf(c, "pd-1").reason())
                    .isEqualTo(ParadigmCatalogService.DOMAIN_UNAVAILABLE);
            assertThat(DomainContext.get()).isNull();
        }
    }

    // =====================================================================================
    // Disclosure of hidden details
    // =====================================================================================

    @Nested
    @DisplayName("hidden details disclosure")
    class Disclosure {

        @BeforeEach
        void kbsAreNotAnonymouslyReadable() {
            give("pd-1", "内部资料", "odn", false, SERVABLE_WITH_KBS);
            when(kbAccessService.authorize(eq("odn"), anyList(), isNull()))
                    .thenThrow(new IllegalArgumentException("kb_not_found"));
            when(knowledgeBaseMapper.selectAccessibleKbIds(eq("odn"), anyList(), isNull()))
                    .thenReturn(List.of());          // neither kb is public
        }

        @Test
        @DisplayName("anonymous caller is told how many, never which")
        void anonymousGetsCountOnly() {
            ParadigmCatalogService.Catalog c = service.build(null, null);

            ParadigmCatalogService.Hidden h = hiddenOf(c, "pd-1");
            assertThat(h.details()).isEmpty();
            assertThat(h.undisclosedCount()).isEqualTo(2);
            // No second query: there is no identity to filter by, so none is issued.
            verify(knowledgeBaseMapper, never()).selectAccessibleKbIds(anyString(), anyList(), anyString());
        }

        @Test
        @DisplayName("identified caller is told only about the KBs they can already see")
        void identifiedCallerSeesOnlyTheirOwn() {
            when(knowledgeBaseMapper.selectAccessibleKbIds(eq("odn"), anyList(), eq("admin")))
                    .thenReturn(List.of("kb-a"));

            ParadigmCatalogService.Catalog c = service.build(null, "admin");

            ParadigmCatalogService.Hidden h = hiddenOf(c, "pd-1");
            assertThat(h.details()).containsExactly("kb-a");
            assertThat(h.undisclosedCount()).as("kb-b stays unnamed").isEqualTo(1);
        }
    }

    // =====================================================================================
    // Domain filter
    // =====================================================================================

    @Nested
    @DisplayName("domain filter")
    class Filter {

        @Test
        @DisplayName("keeps the domain's own paradigms and the domain-agnostic ones")
        void keepsOwnAndAgnostic() {
            give("pd-odn", "odn", "odn", false, SERVABLE);
            give("pd-ccn", "ccn", "cloud_core_network", false, SERVABLE);
            give("pd-any", "any", null, false, SERVABLE);

            ParadigmCatalogService.Catalog c = service.build("odn", null);

            assertThat(idsOf(c.paradigms())).containsExactly("pd-odn", "pd-any");
        }

        @Test
        @DisplayName("filters hidden entries too — another domain's problems are not this one's")
        void filtersHiddenAsWell() {
            give("pd-ccn", "ccn", "cloud_core_network", false, COLLECT_ONLY);

            ParadigmCatalogService.Catalog c = service.build("odn", "admin");

            assertThat(c.paradigms()).isEmpty();
            assertThat(c.hidden()).isEmpty();
        }

        @Test
        @DisplayName("no filter returns every domain")
        void noFilterReturnsEverything() {
            give("pd-odn", "odn", "odn", false, SERVABLE);
            give("pd-ccn", "ccn", "cloud_core_network", false, SERVABLE);

            assertThat(idsOf(service.build(null, null).paradigms()))
                    .containsExactly("pd-odn", "pd-ccn");
        }
    }
}
