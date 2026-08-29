package com.coremasterkb.serving.application;

import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.FullTextRequest;
import com.coremasterkb.serving.domain.FullTextResponse;
import com.coremasterkb.serving.mapper.param.SegmentWindow;
import com.coremasterkb.serving.mapper.result.DocumentFileRow;
import com.coremasterkb.serving.mapper.result.FtsResultRow;
import com.coremasterkb.serving.mapper.result.SegmentFullRow;
import com.coremasterkb.serving.repository.AssetRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

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
    private ScopeResolver scopeResolver;
    private FullTextService service;

    @BeforeEach
    void setUp() {
        repo = mock(AssetRepository.class);
        scopeResolver = mock(ScopeResolver.class);
        service = new FullTextService(repo, scopeResolver,
                new ServingProperties(null, null, DOMAIN, null, null, null, null, null));
    }

    private void scopeIs(ActiveScope scope) {
        when(scopeResolver.resolve(eq(DOMAIN), any(), any(), any(), any(), any())).thenReturn(scope);
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
        scopeIs(releaseScope());
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
    @DisplayName("a rejected scope stops the request before a single row is read")
    void scopeRejectionShortCircuits() {
        // Scope resolution owns the access decision (see ScopeResolverTest); what matters here is
        // that the failure is not caught and softened into an empty-but-successful response.
        when(scopeResolver.resolve(eq(DOMAIN), any(), any(), any(), any(), any()))
                .thenThrow(new IllegalArgumentException("kb_not_found"));

        FullTextRequest request = new FullTextRequest(
                List.of(segRef("seg-1")), DOMAIN, null, null, null, List.of("kb-secret"));

        assertThatThrownBy(() -> service.fetch(request, "mallory"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");

        verify(repo, never()).resolveSegmentsFull(any(), any());
        verify(repo, never()).resolveUnitsFull(any(), any());
    }

    @Test
    @DisplayName("the request's scope inputs are handed to the resolver verbatim")
    void scopeInputsArePassedThrough() {
        scopeIs(releaseScope());
        when(repo.resolveSegmentsFull(any(), any())).thenReturn(List.of());
        stubEmptyFiles();

        FullTextRequest request = new FullTextRequest(
                List.of(segRef("seg-1")), DOMAIN, "staging", "p-1", 3, null);
        service.fetch(request, "alice");

        verify(scopeResolver).resolve(DOMAIN, "staging", "p-1", 3, List.of(), "alice");
    }

    // ---------------------------------------------------------------- misses

    @Test
    @DisplayName("an id outside the scope reads exactly like an id that never existed")
    void outOfScopeAndNonexistentAreIndistinguishable() {
        scopeIs(releaseScope());
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
        scopeIs(releaseScope());
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

        scopeIs(releaseScope());
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

        scopeIs(releaseScope());
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

        scopeIs(releaseScope());
        when(repo.resolveUnitsFull(any(), any())).thenReturn(List.of(unit));
        stubEmptyFiles();

        FullTextResponse res = service.fetch(req(List.of(unitRef("ru-1"))), "alice");

        assertThat(res.items().get(0).found()).isTrue();
        assertThat(res.items().get(0).segments()).isEmpty();
        verify(repo, never()).resolveSegmentsFull(any(), any());
    }

    // ---------------------------------------------------------------- window

    private static SegmentFullRow indexed(String id, String snapshotId, int index, String text) {
        SegmentFullRow row = segment(id, snapshotId, text);
        row.setSegmentIndex(index);
        return row;
    }

    private static FullTextRequest windowReq(List<FullTextRequest.Ref> refs, int radius) {
        return new FullTextRequest(refs, DOMAIN, null, null, null, null, "window", radius);
    }

    @Test
    @DisplayName("segment granularity asks for no neighbours at all")
    void segmentGranularitySkipsWindowQuery() {
        scopeIs(releaseScope());
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(indexed("seg-5", "snap-1", 5, "命中段")));
        stubEmptyFiles();

        service.fetch(req(List.of(segRef("seg-5"))), "alice");

        verify(repo, never()).resolveSegmentWindows(any(), any());
    }

    @Test
    @DisplayName("window mode returns neighbours in reading order, roles marked around the target")
    void windowReturnsNeighboursInOrder() {
        scopeIs(releaseScope());
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(indexed("seg-5", "snap-1", 5, "命中段")));
        when(repo.resolveSegmentWindows(any(), any())).thenReturn(List.of(
                indexed("seg-4", "snap-1", 4, "上一段"),
                indexed("seg-5", "snap-1", 5, "命中段"),
                indexed("seg-6", "snap-1", 6, "下一段")));
        stubEmptyFiles();

        FullTextResponse res = service.fetch(windowReq(List.of(segRef("seg-5")), 1), "alice");

        // Reading order, not targets-first: the point of a window is continuous prose.
        assertThat(res.items().get(0).segments())
                .extracting(FullTextResponse.Segment::id)
                .containsExactly("seg-4", "seg-5", "seg-6");
        assertThat(res.items().get(0).segments())
                .extracting(FullTextResponse.Segment::role)
                .containsExactly("before", "target", "after");
    }

    @Test
    @DisplayName("the requested radius bounds the window it asks the database for")
    void windowRadiusBoundsTheQuery() {
        scopeIs(releaseScope());
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(indexed("seg-5", "snap-1", 5, "命中段")));
        when(repo.resolveSegmentWindows(any(), any())).thenReturn(List.of());
        stubEmptyFiles();

        service.fetch(windowReq(List.of(segRef("seg-5")), 2), "alice");

        ArgumentCaptor<List<SegmentWindow>> captor = ArgumentCaptor.forClass(List.class);
        verify(repo).resolveSegmentWindows(captor.capture(), any());
        assertThat(captor.getValue())
                .containsExactly(new SegmentWindow("snap-1", 3, 7));
    }

    @Test
    @DisplayName("a target at index 0 does not ask for negative indexes")
    void windowClampsAtDocumentStart() {
        scopeIs(releaseScope());
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(indexed("seg-0", "snap-1", 0, "首段")));
        when(repo.resolveSegmentWindows(any(), any())).thenReturn(List.of());
        stubEmptyFiles();

        service.fetch(windowReq(List.of(segRef("seg-0")), 2), "alice");

        ArgumentCaptor<List<SegmentWindow>> captor = ArgumentCaptor.forClass(List.class);
        verify(repo).resolveSegmentWindows(captor.capture(), any());
        assertThat(captor.getValue().get(0).fromIndex()).isZero();
    }

    @Test
    @DisplayName("a neighbour from another document is not pulled in by index alone")
    void windowDoesNotCrossDocuments() {
        scopeIs(releaseScope());
        when(repo.resolveSegmentsFull(any(), any()))
                .thenReturn(List.of(indexed("seg-5", "snap-1", 5, "命中段")));
        // Same index, different snapshot — the mapper is scope-filtered, not window-filtered,
        // so proximity has to be checked per snapshot rather than on the index alone.
        when(repo.resolveSegmentWindows(any(), any())).thenReturn(List.of(
                indexed("other-4", "snap-2", 4, "别的文档"),
                indexed("seg-6", "snap-1", 6, "下一段")));
        stubEmptyFiles();

        FullTextResponse res = service.fetch(windowReq(List.of(segRef("seg-5")), 1), "alice");

        assertThat(res.items().get(0).segments())
                .extracting(FullTextResponse.Segment::id)
                .containsExactly("seg-5", "seg-6");
    }

    @Test
    @DisplayName("a segment that is both target and neighbour appears once, as the target")
    void overlappingWindowsDoNotDuplicate() {
        scopeIs(releaseScope());
        when(repo.resolveSegmentsFull(any(), any())).thenReturn(List.of(
                indexed("seg-5", "snap-1", 5, "命中段甲"),
                indexed("seg-6", "snap-1", 6, "命中段乙")));
        when(repo.resolveSegmentWindows(any(), any())).thenReturn(List.of(
                indexed("seg-5", "snap-1", 5, "命中段甲"),
                indexed("seg-6", "snap-1", 6, "命中段乙"),
                indexed("seg-7", "snap-1", 7, "下一段")));
        stubEmptyFiles();

        FullTextResponse res = service.fetch(
                windowReq(List.of(segRef("seg-5"), segRef("seg-6")), 1), "alice");

        assertThat(res.items().get(0).segments())
                .extracting(FullTextResponse.Segment::id)
                .containsExactly("seg-5", "seg-6");
        assertThat(res.items().get(0).segments().get(0).role()).isEqualTo("target");
    }

    @Test
    @DisplayName("granularity and radius are validated at the request boundary")
    void invalidWindowArgumentsAreRejected() {
        List<FullTextRequest.Ref> refs = List.of(segRef("seg-1"));

        assertThatThrownBy(() ->
                new FullTextRequest(refs, DOMAIN, null, null, null, null, "paragraph", null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("unknown_granularity");

        assertThatThrownBy(() ->
                new FullTextRequest(refs, DOMAIN, null, null, null, null, "window", 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("window_radius_out_of_range");

        // Bounded so one request cannot walk a whole document a few neighbours at a time.
        assertThatThrownBy(() ->
                new FullTextRequest(refs, DOMAIN, null, null, null, null, "window", 6))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("window_radius_out_of_range");
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

        scopeIs(releaseScope());
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

        scopeIs(releaseScope());
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

        scopeIs(shared);
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
