package com.coremasterkb.serving.application;

import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.FullTextRequest;
import com.coremasterkb.serving.domain.FullTextResponse;
import com.coremasterkb.serving.domainpack.DomainRegistry;
import com.coremasterkb.serving.mapper.result.DocumentFileRow;
import com.coremasterkb.serving.mapper.result.FtsResultRow;
import com.coremasterkb.serving.mapper.result.SegmentFullRow;
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
 * The behaviour worth pinning here is not "does it return text" but the scope invariant: ids come
 * from the caller, so nothing may be returned that the request's own scope did not authorize, and
 * an empty scope must fail rather than widen.
 */
@DisplayName("FullTextService")
class FullTextServiceTest {

    private static final String DOMAIN = "cloud_core_network";

    private AssetRepository repo;
    private KbAccessService kbAccess;
    private ParadigmService paradigmService;
    private DomainRegistry registry;
    private FullTextService service;

    @BeforeEach
    void setUp() {
        repo = mock(AssetRepository.class);
        kbAccess = mock(KbAccessService.class);
        paradigmService = mock(ParadigmService.class);
        registry = mock(DomainRegistry.class);
        when(registry.getDefaultChannel(anyString())).thenReturn("prod");

        service = new FullTextService(repo, kbAccess, paradigmService, registry,
                new ServingProperties(null, null, DOMAIN, null, null));
    }

    // ---------------------------------------------------------------- helpers

    private static FullTextRequest req(List<FullTextRequest.Ref> refs) {
        return new FullTextRequest(refs, DOMAIN, null, null, null, null);
    }

    private static FullTextRequest.Ref segRef(String id) {
        return new FullTextRequest.Ref(FullTextRequest.TYPE_RAW_SEGMENT, id);
    }

    private static FullTextRequest.Ref unitRef(String id) {
        return new FullTextRequest.Ref(FullTextRequest.TYPE_RETRIEVAL_UNIT, id);
    }

    private static SegmentFullRow segment(String id, String snapshotId, String text) {
        SegmentFullRow row = new SegmentFullRow();
        row.setId(id);
        row.setDocumentSnapshotId(snapshotId);
        row.setSegmentIndex(7);
        row.setRawText(text);
        row.setBlockType("paragraph");
        row.setSemanticRole("parameter");
        row.setSectionPath("[\"3\",\"3.2\"]");
        row.setSectionTitle("注册管理");
        return row;
    }

    private static ActiveScope releaseScope() {
        return new ActiveScope("rel-1", "build-1", List.of("snap-1"), Map.of("doc-1", "snap-1"));
    }

    private void stubEmptyFiles() {
        when(repo.resolveFileLocations(any(), any())).thenReturn(List.of());
    }

    // ---------------------------------------------------------------- scope

    @Test
    @DisplayName("segment text comes back uncompressed, with the scope echoed")
    void returnsFullSegmentText() {
        String longText = "完整原文".repeat(500);
        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any())).thenReturn(releaseScope());
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(segment("seg-1", "snap-1", longText)));
        stubEmptyFiles();

        FullTextResponse res = service.fetch(req(List.of(segRef("seg-1"))), "alice");

        assertThat(res.items()).hasSize(1);
        FullTextResponse.Item item = res.items().get(0);
        assertThat(item.found()).isTrue();
        assertThat(item.segments()).hasSize(1);
        assertThat(item.segments().get(0).text()).isEqualTo(longText);
        assertThat(item.segments().get(0).sectionPath()).containsExactly("3", "3.2");
        assertThat(res.scope().releaseId()).isEqualTo("rel-1");
        assertThat(res.scope().snapshotCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("scope that resolves to zero snapshots fails instead of reading everything")
    void emptyScopeIsRejected() {
        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any()))
                .thenReturn(new ActiveScope("rel-1", "build-1", List.of(), Map.of()));

        assertThatThrownBy(() -> service.fetch(req(List.of(segRef("seg-1"))), "alice"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("empty_scope");

        verify(repo, never()).resolveSegmentsFull(any(), any());
    }

    @Test
    @DisplayName("an unauthorized kbId fails the whole request — no partial answer")
    void unauthorizedKbFailsEverything() {
        when(kbAccess.authorize(eq(DOMAIN), any(), any()))
                .thenThrow(new IllegalArgumentException("kb_not_found"));

        FullTextRequest request = new FullTextRequest(
                List.of(segRef("seg-1")), DOMAIN, null, null, null, List.of("kb-secret"));

        assertThatThrownBy(() -> service.fetch(request, "mallory"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");

        verify(repo, never()).resolveSegmentsFull(any(), any());
        verify(repo, never()).resolveActiveScope(anyString(), anyString(), any());
    }

    @Test
    @DisplayName("paradigmId and kbIds together are rejected rather than silently resolved")
    void conflictingScopeSource() {
        FullTextRequest request = new FullTextRequest(
                List.of(segRef("seg-1")), DOMAIN, null, "p-1", null, List.of("kb-a"));

        assertThatThrownBy(() -> service.fetch(request, "alice"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("conflicting_scope_source");

        verifyNoInteractions(kbAccess);
    }

    @Test
    @DisplayName("paradigmId reads kbIds off the stored scope_resolve node, then authorizes them")
    void paradigmScopeIsAuthorizedNotTrusted() throws Exception {
        String graph = """
                {"nodes":[
                  {"nodeId":"sr","operatorType":"scope_resolve","params":{"kbIds":["kb-a","kb-b"]}},
                  {"nodeId":"q","operatorType":"query_understanding","params":{}}
                ]}""";
        when(paradigmService.resolveExecutableGraph(eq("p-1"), any()))
                .thenReturn(new ObjectMapper().readTree(graph));
        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of("kb-a", "kb-b"));
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any()))
                .thenReturn(new ActiveScope("kb:kb-a,kb-b", null, List.of("snap-1"), Map.of()));
        when(repo.resolveSegmentsFull(any(), any())).thenReturn(List.of());
        stubEmptyFiles();

        FullTextRequest request = new FullTextRequest(
                List.of(segRef("seg-1")), DOMAIN, null, "p-1", null, null);
        service.fetch(request, "alice");

        // The stored graph supplies the ids; the caller's identity still decides.
        verify(kbAccess).authorize(DOMAIN, List.of("kb-a", "kb-b"), "alice");
    }

    // ---------------------------------------------------------------- misses

    @Test
    @DisplayName("an id outside the scope reads exactly like an id that never existed")
    void outOfScopeAndNonexistentAreIndistinguishable() {
        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any())).thenReturn(releaseScope());
        // Both ids fall outside the scope filter, so the mapper returns neither.
        when(repo.resolveSegmentsFull(any(), any())).thenReturn(List.of());
        stubEmptyFiles();

        FullTextResponse res = service.fetch(
                req(List.of(segRef("seg-private"), segRef("seg-never-existed"))), "alice");

        assertThat(res.items()).hasSize(2);
        assertThat(res.items()).allSatisfy(item -> {
            assertThat(item.found()).isFalse();
            assertThat(item.reason()).isEqualTo("out_of_scope");
            assertThat(item.segments()).isEmpty();
            assertThat(item.unit()).isNull();
        });
    }

    @Test
    @DisplayName("one stale ref does not discard the others")
    void partialMissKeepsGoodResults() {
        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any())).thenReturn(releaseScope());
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(segment("seg-1", "snap-1", "有效原文")));
        stubEmptyFiles();

        FullTextResponse res = service.fetch(
                req(List.of(segRef("seg-1"), segRef("seg-gone"))), "alice");

        assertThat(res.items().get(0).found()).isTrue();
        assertThat(res.items().get(1).found()).isFalse();
    }

    @Test
    @DisplayName("more than MAX_REFS refs is rejected before any query runs")
    void refCapEnforced() {
        List<FullTextRequest.Ref> refs = new java.util.ArrayList<>();
        for (int i = 0; i <= FullTextService.MAX_REFS; i++) {
            refs.add(segRef("seg-" + i));
        }

        assertThatThrownBy(() -> service.fetch(req(refs), "alice"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("too_many_refs");

        verifyNoInteractions(repo);
    }

    // ---------------------------------------------------------------- units

    @Test
    @DisplayName("a unit ref returns its own text plus the segments behind it")
    void unitExpandsToSegments() {
        FtsResultRow unit = new FtsResultRow();
        unit.setId("ru-1");
        unit.setDocumentSnapshotId("snap-1");
        unit.setText("单元全文");
        unit.setTitle("AMF 注册流程");
        unit.setUnitType("qa");
        unit.setSourceRefsJson("{\"raw_segment_ids\":[\"seg-1\"]}");

        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any())).thenReturn(releaseScope());
        when(repo.resolveUnitsFull(any(), any())).thenReturn(List.of(unit));
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(segment("seg-1", "snap-1", "段落原文")));
        stubEmptyFiles();

        FullTextResponse res = service.fetch(req(List.of(unitRef("ru-1"))), "alice");

        FullTextResponse.Item item = res.items().get(0);
        assertThat(item.found()).isTrue();
        assertThat(item.unit().text()).isEqualTo("单元全文");
        assertThat(item.segments()).extracting(FullTextResponse.Segment::id).containsExactly("seg-1");
    }

    @Test
    @DisplayName("target_ref_json's singular raw_segment_id form is followed too")
    void unitFallsBackToSingularTargetRef() {
        FtsResultRow unit = new FtsResultRow();
        unit.setId("ru-1");
        unit.setDocumentSnapshotId("snap-1");
        unit.setText("单元全文");
        unit.setSourceRefsJson("{}");
        // table_row / figure units point at one segment with the singular key; a reader that only
        // knew "raw_segment_ids" would report no provenance for them at all.
        unit.setTargetRefJson("{\"raw_segment_id\":\"seg-1\"}");

        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any())).thenReturn(releaseScope());
        when(repo.resolveUnitsFull(any(), any())).thenReturn(List.of(unit));
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(segment("seg-1", "snap-1", "段落原文")));
        stubEmptyFiles();

        FullTextResponse res = service.fetch(req(List.of(unitRef("ru-1"))), "alice");

        assertThat(res.items().get(0).segments())
                .extracting(FullTextResponse.Segment::id).containsExactly("seg-1");
    }

    @Test
    @DisplayName("a unit whose segments are gone still returns its own stored text")
    void unitWithoutResolvableSegments() {
        FtsResultRow unit = new FtsResultRow();
        unit.setId("ru-1");
        unit.setDocumentSnapshotId("snap-1");
        unit.setText("单元全文");
        unit.setSourceRefsJson("{}");

        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any())).thenReturn(releaseScope());
        when(repo.resolveUnitsFull(any(), any())).thenReturn(List.of(unit));
        stubEmptyFiles();

        FullTextResponse res = service.fetch(req(List.of(unitRef("ru-1"))), "alice");

        assertThat(res.items().get(0).found()).isTrue();
        assertThat(res.items().get(0).segments()).isEmpty();
        verify(repo, never()).resolveSegmentsFull(any(), any());
    }

    // ---------------------------------------------------- document attribution

    @Test
    @DisplayName("document attribution comes from the scope, carrying kbId and hasRawFile")
    void attributesDocumentFromScope() {
        DocumentFileRow file = new DocumentFileRow();
        file.setId("doc-1");
        file.setKbId("kb-a");
        file.setStoragePath("/app/uploads/kb-a/spec.pdf");
        file.setDocumentName("spec.pdf");
        file.setDocumentKey("doc:/spec.pdf");

        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any())).thenReturn(releaseScope());
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(segment("seg-1", "snap-1", "原文")));
        when(repo.resolveFileLocations(any(), any())).thenReturn(List.of(file));

        FullTextResponse res = service.fetch(req(List.of(segRef("seg-1"))), "alice");

        FullTextResponse.Segment seg = res.items().get(0).segments().get(0);
        assertThat(seg.documentId()).isEqualTo("doc-1");
        assertThat(seg.kbId()).isEqualTo("kb-a");
        assertThat(seg.documentName()).isEqualTo("spec.pdf");
        assertThat(seg.hasRawFile()).isTrue();
    }

    @Test
    @DisplayName("a legacy document with no uploaded file reports hasRawFile=false")
    void legacyDocumentHasNoRawFile() {
        DocumentFileRow file = new DocumentFileRow();
        file.setId("doc-1");
        file.setDocumentKey("doc:/legacy.md");
        // kb_id and storage_path stay null: ingested through /api/runs, never uploaded to a KB.

        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any())).thenReturn(releaseScope());
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(segment("seg-1", "snap-1", "原文")));
        when(repo.resolveFileLocations(any(), any())).thenReturn(List.of(file));

        FullTextResponse res = service.fetch(req(List.of(segRef("seg-1"))), "alice");

        assertThat(res.items().get(0).segments().get(0).hasRawFile()).isFalse();
        assertThat(res.items().get(0).segments().get(0).kbId()).isNull();
    }

    @Test
    @DisplayName("a snapshot shared by two documents is left unattributed rather than guessed")
    void sharedSnapshotIsNotGuessed() {
        // Content-level dedup: asset_document_snapshots is UNIQUE(domain, normalized_content_hash),
        // so identical files share one snapshot and the segment cannot name a single document.
        ActiveScope shared = new ActiveScope(
                "rel-1", "build-1", List.of("snap-1"),
                Map.of("doc-1", "snap-1", "doc-2", "snap-1"));

        when(kbAccess.authorize(eq(DOMAIN), any(), any())).thenReturn(List.of());
        when(repo.resolveActiveScope(eq(DOMAIN), eq("prod"), any())).thenReturn(shared);
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(segment("seg-1", "snap-1", "原文")));

        FullTextResponse res = service.fetch(req(List.of(segRef("seg-1"))), "alice");

        FullTextResponse.Segment seg = res.items().get(0).segments().get(0);
        assertThat(seg.documentId()).isNull();
        assertThat(seg.hasRawFile()).isFalse();
        // No point asking the DB for a document we could not identify.
        verify(repo, never()).resolveFileLocations(any(), any());
    }
}
