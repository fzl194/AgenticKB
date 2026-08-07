package com.coremasterkb.serving.application;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domainpack.DomainRegistry;
import com.coremasterkb.serving.operator.paradigm.ParadigmService;
import com.coremasterkb.serving.repository.AssetRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

/**
 * This class is where the access decision for every drill-down endpoint is made, so these tests
 * are the ones that would catch a widened scope.
 */
@DisplayName("ScopeResolver")
class ScopeResolverTest {

    private static final String DOMAIN = "cloud_core_network";

    private AssetRepository repo;
    private KbAccessService kbAccess;
    private ParadigmService paradigmService;
    private ScopeResolver resolver;

    @BeforeEach
    void setUp() {
        repo = mock(AssetRepository.class);
        kbAccess = mock(KbAccessService.class);
        paradigmService = mock(ParadigmService.class);
        DomainRegistry registry = mock(DomainRegistry.class);
        when(registry.getDefaultChannel(anyString())).thenReturn("prod");
        resolver = new ScopeResolver(repo, kbAccess, paradigmService, registry);
    }

    private static ActiveScope scope(List<String> snapshotIds) {
        return new ActiveScope("rel-1", "build-1", snapshotIds, Map.of());
    }

    @Test
    @DisplayName("no KB named — the ordinary domain-wide release, channel from the registry")
    void domainWideByDefault() {
        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any()))
                .thenReturn(scope(List.of("snap-1")));

        ActiveScope result = resolver.resolve(DOMAIN, null, null, null, null, "alice");

        assertThat(result.snapshotIds()).containsExactly("snap-1");
        verify(repo).resolveActiveScope(DOMAIN, "prod", List.of());
    }

    @Test
    @DisplayName("an explicit channel beats the registry default")
    void explicitChannelWins() {
        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("staging"), any()))
                .thenReturn(scope(List.of("snap-1")));

        resolver.resolve(DOMAIN, "staging", null, null, null, "alice");

        verify(repo).resolveActiveScope(DOMAIN, "staging", List.of());
    }

    @Test
    @DisplayName("an unauthorized kbId fails before any scope is resolved")
    void unauthorizedKbFailsFirst() {
        when(kbAccess.authorize(eq(DOMAIN), any(), any()))
                .thenThrow(new IllegalArgumentException("kb_not_found"));

        assertThatThrownBy(() ->
                resolver.resolve(DOMAIN, null, null, null, List.of("kb-secret"), "mallory"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");

        verify(repo, never()).resolveActiveScope(anyString(), anyString(), any());
    }

    @Test
    @DisplayName("paradigmId and kbIds together are rejected rather than silently resolved")
    void conflictingScopeSource() {
        assertThatThrownBy(() ->
                resolver.resolve(DOMAIN, null, "p-1", null, List.of("kb-a"), "alice"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("conflicting_scope_source");

        verifyNoInteractions(kbAccess, paradigmService, repo);
    }

    @Test
    @DisplayName("paradigmId reads kbIds off the stored graph, and still authorizes them")
    void paradigmScopeIsAuthorizedNotTrusted() throws Exception {
        String graph = """
                {"nodes":[
                  {"nodeId":"sr","operatorType":"scope_resolve","params":{"kbIds":["kb-a","kb-b"]}},
                  {"nodeId":"q","operatorType":"query_understanding","params":{}}
                ]}""";
        when(paradigmService.resolveExecutableGraph(eq("p-1"), any()))
                .thenReturn(new ObjectMapper().readTree(graph));
        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of("kb-a", "kb-b"));
        when(repo.resolveActiveScope(eq(DOMAIN), any(), any())).thenReturn(scope(List.of("snap-1")));

        resolver.resolve(DOMAIN, null, "p-1", null, null, "alice");

        // A stored graph supplies the ids; the caller's identity still decides. Otherwise saving a
        // paradigm would be a way to read knowledge bases you cannot open.
        verify(kbAccess).authorize(DOMAIN, List.of("kb-a", "kb-b"), "alice");
    }

    @Test
    @DisplayName("a paradigm whose scope_resolve names no KB resolves domain-wide, not to nothing")
    void paradigmWithoutKbIds() throws Exception {
        String graph = """
                {"nodes":[{"nodeId":"sr","operatorType":"scope_resolve","params":{}}]}""";
        when(paradigmService.resolveExecutableGraph(eq("p-1"), any()))
                .thenReturn(new ObjectMapper().readTree(graph));
        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), any(), any())).thenReturn(scope(List.of("snap-1")));

        resolver.resolve(DOMAIN, null, "p-1", null, null, "alice");

        verify(kbAccess).authorize(DOMAIN, List.of(), "alice");
    }

    @Test
    @DisplayName("a scope with zero snapshots fails instead of becoming an unfiltered read")
    void emptyScopeIsRejected() {
        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), any(), any())).thenReturn(scope(List.of()));

        assertThatThrownBy(() -> resolver.resolve(DOMAIN, null, null, null, null, "alice"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("empty_scope");
    }
}
