package com.coremasterkb.serving.repository;

import com.coremasterkb.serving.AgentServingApplication;
import com.coremasterkb.serving.domain.ActiveScope;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Verifies KB-narrowed scope resolution against a real PostgreSQL, because every guarantee it makes
 * lives in SQL (DISTINCT ON ordering, post-DISTINCT filtering, the kb ownership join) and none of it
 * is observable from a mocked mapper.
 *
 * <p>Seeds its own fixture graph under a per-run token and deletes it afterwards, so it neither
 * depends on nor disturbs whatever else is in the database.</p>
 */
@SpringBootTest(classes = AgentServingApplication.class)
@ActiveProfiles("test-pg")
@Tag("pg-integration")
@DisplayName("AssetRepository KB scope IT")
class AssetRepositoryKbScopeIT {

    private static final String DOMAIN = "cloud_core_network";
    private static final String OTHER_DOMAIN = "generic";

    @Autowired
    private DataSource dataSource;

    @Autowired
    private AssetRepository assetRepository;

    private JdbcTemplate jdbc;
    private String token;

    // Fixture ids, all suffixed with the run token.
    private String userId, kbA, kbB, kbC;
    private String docA1, docA2, docA3, docA4, docB1, docC1;
    private String snapA1Old, snapA1New, snapA2, snapA3, snapA4, snapC1;
    private String buildA1, buildA2, buildA3Building, buildB1, buildForeign, buildC1;

    @BeforeEach
    void setUp() {
        try (Connection conn = dataSource.getConnection()) {
            assumeTrue(conn.isValid(3), "PostgreSQL not reachable — skipping");
        } catch (SQLException e) {
            assumeTrue(false, "PostgreSQL not reachable — skipping");
        }
        jdbc = new JdbcTemplate(dataSource);

        assumeTrue(tableExists("knowledge_bases") && tableExists("kb_users"),
                "kb schema not present in this database — skipping");
        assumeTrue(columnExists("asset_builds", "kb_id") && columnExists("asset_documents", "kb_id"),
                "kb columns not present on asset tables — skipping");

        token = UUID.randomUUID().toString().substring(0, 8);
        assignIds();
        seed();
    }

    @AfterEach
    void cleanUp() {
        if (jdbc == null || token == null) return;
        // FK order: selections (RESTRICT → snapshots) before builds/documents, documents before KBs.
        jdbc.update("DELETE FROM asset_build_document_snapshots WHERE build_id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM asset_builds WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM asset_documents WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM asset_document_snapshots WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM knowledge_bases WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM kb_users WHERE id LIKE ?", "%" + token);
    }

    // -------------------------------------------------------------------------
    // Tests
    // -------------------------------------------------------------------------

    @Test
    @DisplayName("resolves each document's snapshot from the newest build containing it")
    void resolvesLatestSnapshotPerDocument() {
        ActiveScope scope = assetRepository.resolveKbScope(DOMAIN, List.of(kbA));

        // docA1 was re-mined: the second build's snapshot wins, the first is gone.
        assertThat(scope.documentSnapshotMap()).containsEntry(docA1, snapA1New);
        assertThat(scope.snapshotIds()).doesNotContain(snapA1Old);

        // docA3 only ever appeared in the older build — still resolvable, which is the whole point
        // of resolving per document rather than taking one "latest build" wholesale.
        assertThat(scope.documentSnapshotMap()).containsEntry(docA3, snapA3);

        // Other KBs stay out.
        assertThat(scope.documentSnapshotMap()).doesNotContainKey(docB1);
    }

    @Test
    @DisplayName("a document removed by a later build does not fall back to its older active row")
    void excludesDocumentRemovedByLaterBuild() {
        ActiveScope scope = assetRepository.resolveKbScope(DOMAIN, List.of(kbA));

        assertThat(scope.documentSnapshotMap()).doesNotContainKey(docA2);
        assertThat(scope.snapshotIds()).doesNotContain(snapA2);
    }

    @Test
    @DisplayName("only validated/published builds count — a building build is invisible")
    void ignoresUnfinishedBuilds() {
        ActiveScope scope = assetRepository.resolveKbScope(DOMAIN, List.of(kbA));

        assertThat(scope.documentSnapshotMap()).doesNotContainKey(docA4);
        assertThat(scope.snapshotIds()).doesNotContain(snapA4);
    }

    @Test
    @DisplayName("a sibling KB's build cannot pin a document's snapshot")
    void foreignKbBuildCannotPinSnapshot() {
        // buildForeign belongs to kbB, is the newest build in the fixture, and carries a stale
        // selection for docA1 (a kbA document) — exactly the shape parent carry-forward produces.
        // Without the b.kb_id = d.kb_id join it would win on created_at and pin snapA1Old.
        ActiveScope scope = assetRepository.resolveKbScope(DOMAIN, List.of(kbA, kbB));

        assertThat(scope.documentSnapshotMap()).containsEntry(docA1, snapA1New);
        assertThat(scope.snapshotIds()).doesNotContain(snapA1Old);
    }

    @Test
    @DisplayName("multiple KBs union, and shared snapshots are de-duplicated")
    void unionsMultipleKbsAndDeduplicatesSnapshots() {
        ActiveScope scope = assetRepository.resolveKbScope(DOMAIN, List.of(kbA, kbB));

        assertThat(scope.documentSnapshotMap()).containsKeys(docA1, docA3, docB1);
        // docB1 deliberately shares snapA3 with docA3 (content-level dedup is real in this schema).
        assertThat(scope.documentSnapshotMap()).containsEntry(docB1, snapA3);
        assertThat(scope.snapshotIds()).doesNotHaveDuplicates();
        assertThat(scope.snapshotIds()).contains(snapA3);
    }

    @Test
    @DisplayName("a KB from another domain resolves to nothing")
    void rejectsKbFromAnotherDomain() {
        // kbC and its content live in OTHER_DOMAIN. Production runs every domain in one physical
        // database, so the domain guard is the only thing preventing a cross-domain read here.
        assertThatThrownBy(() -> assetRepository.resolveKbScope(DOMAIN, List.of(kbC)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("no_active_kb_build");

        ActiveScope ownDomain = assetRepository.resolveKbScope(OTHER_DOMAIN, List.of(kbC));
        assertThat(ownDomain.documentSnapshotMap()).containsEntry(docC1, snapC1);
    }

    @Test
    @DisplayName("scope key is order-independent and buildId is null")
    void scopeKeyPartitionsPerKbSelection() {
        ActiveScope ab = assetRepository.resolveKbScope(DOMAIN, List.of(kbA, kbB));
        ActiveScope ba = assetRepository.resolveKbScope(DOMAIN, List.of(kbB, kbA));
        ActiveScope aOnly = assetRepository.resolveKbScope(DOMAIN, List.of(kbA));

        // The semantic cache partitions on releaseId, so [A,B] and [B,A] must collapse to one
        // bucket while [A] must not share theirs.
        assertThat(ab.releaseId()).isEqualTo(ba.releaseId());
        assertThat(ab.releaseId()).isNotEqualTo(aOnly.releaseId());
        assertThat(ab.releaseId()).startsWith("kb:");
        assertThat(ab.buildId()).isNull();
    }

    @Test
    @DisplayName("blank kb id list is rejected rather than silently matching everything")
    void rejectsEmptyKbIds() {
        assertThatThrownBy(() -> assetRepository.resolveKbScope(DOMAIN, List.of("", "   ")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_ids_required");
    }

    @Test
    @DisplayName("empty kbIds on the 3-arg overload delegates to the release path unchanged")
    void emptyKbIdsDelegatesToReleasePath() {
        // A domain with no release must fail the same way through both entry points — proof the
        // overload delegates rather than quietly resolving an empty KB scope.
        String unknownDomain = "domain_without_release_" + token;

        assertThatThrownBy(() -> assetRepository.resolveActiveScope(unknownDomain, "prod", List.of()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("no_active_release");
        assertThatThrownBy(() -> assetRepository.resolveActiveScope(unknownDomain, "prod", null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("no_active_release");
        assertThatThrownBy(() -> assetRepository.resolveActiveScope(unknownDomain, "prod"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("no_active_release");
    }

    // -------------------------------------------------------------------------
    // Fixture
    // -------------------------------------------------------------------------

    private void assignIds() {
        userId = "u-" + token;
        kbA = "kbA-" + token;
        kbB = "kbB-" + token;
        kbC = "kbC-" + token;
        docA1 = "docA1-" + token;
        docA2 = "docA2-" + token;
        docA3 = "docA3-" + token;
        docA4 = "docA4-" + token;
        docB1 = "docB1-" + token;
        docC1 = "docC1-" + token;
        snapA1Old = "snapA1old-" + token;
        snapA1New = "snapA1new-" + token;
        snapA2 = "snapA2-" + token;
        snapA3 = "snapA3-" + token;
        snapA4 = "snapA4-" + token;
        snapC1 = "snapC1-" + token;
        buildA1 = "buildA1-" + token;
        buildA2 = "buildA2-" + token;
        buildA3Building = "buildA3-" + token;
        buildB1 = "buildB1-" + token;
        buildForeign = "buildFgn-" + token;
        buildC1 = "buildC1-" + token;
    }

    private void seed() {
        insertUser(userId);
        insertKb(kbA, DOMAIN, "KB A " + token);
        insertKb(kbB, DOMAIN, "KB B " + token);
        insertKb(kbC, OTHER_DOMAIN, "KB C " + token);

        insertDocument(docA1, DOMAIN, kbA, "doc:/a1.md");
        insertDocument(docA2, DOMAIN, kbA, "doc:/a2.md");
        insertDocument(docA3, DOMAIN, kbA, "doc:/a3.md");
        insertDocument(docA4, DOMAIN, kbA, "doc:/a4.md");
        insertDocument(docB1, DOMAIN, kbB, "doc:/b1.md");
        insertDocument(docC1, OTHER_DOMAIN, kbC, "doc:/c1.md");

        insertSnapshot(snapA1Old, DOMAIN);
        insertSnapshot(snapA1New, DOMAIN);
        insertSnapshot(snapA2, DOMAIN);
        insertSnapshot(snapA3, DOMAIN);
        insertSnapshot(snapA4, DOMAIN);
        insertSnapshot(snapC1, OTHER_DOMAIN);

        // Timeline: A1 (oldest) → A2 → B1 → building → foreign (newest).
        insertBuild(buildA1, DOMAIN, kbA, "validated", "2026-01-01T00:00:00Z");
        insertBuild(buildA2, DOMAIN, kbA, "published", "2026-02-01T00:00:00Z");
        insertBuild(buildA3Building, DOMAIN, kbA, "building", "2026-03-01T00:00:00Z");
        insertBuild(buildB1, DOMAIN, kbB, "validated", "2026-02-15T00:00:00Z");
        insertBuild(buildForeign, DOMAIN, kbB, "published", "2026-06-01T00:00:00Z");
        insertBuild(buildC1, OTHER_DOMAIN, kbC, "validated", "2026-01-15T00:00:00Z");

        // Older KB-A build: a1 (superseded later), a2 (removed later), a3 (never revisited).
        insertSelection(buildA1, docA1, snapA1Old, "active", "add");
        insertSelection(buildA1, docA2, snapA2, "active", "add");
        insertSelection(buildA1, docA3, snapA3, "active", "add");

        // Newer KB-A build: a1 re-mined, a2 withdrawn.
        insertSelection(buildA2, docA1, snapA1New, "active", "update");
        insertSelection(buildA2, docA2, snapA2, "removed", "remove");

        // Unfinished build — must not contribute.
        insertSelection(buildA3Building, docA4, snapA4, "active", "add");

        // KB B shares snapA3 with docA3 to exercise snapshot de-duplication.
        insertSelection(buildB1, docB1, snapA3, "active", "add");

        // Newest build overall, owned by KB B but holding a stale KB-A selection.
        insertSelection(buildForeign, docA1, snapA1Old, "active", "retain");

        insertSelection(buildC1, docC1, snapC1, "active", "add");
    }

    private void insertUser(String id) {
        jdbc.update("INSERT INTO kb_users (id, username, display_name, status, created_at) "
                        + "VALUES (?, ?, ?, 'active', ?)",
                id, "tester-" + token, "Tester", "2026-01-01T00:00:00Z");
    }

    private void insertKb(String id, String domain, String name) {
        jdbc.update("INSERT INTO knowledge_bases "
                        + "(id, domain, name, owner_id, visibility, status, created_at, updated_at) "
                        + "VALUES (?, ?, ?, ?, 'private', 'active', ?, ?)",
                id, domain, name, userId, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z");
    }

    private void insertDocument(String id, String domain, String kbId, String documentKey) {
        jdbc.update("INSERT INTO asset_documents "
                        + "(id, domain, document_key, document_name, created_at, kb_id, "
                        + " storage_path, directory_path, owner_id) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?, '', ?)",
                id, domain, documentKey, id + ".md", "2026-01-01T00:00:00Z", kbId,
                "/tmp/" + kbId + "/" + id + ".md", userId);
    }

    private void insertSnapshot(String id, String domain) {
        jdbc.update("INSERT INTO asset_document_snapshots "
                        + "(id, domain, normalized_content_hash, raw_content_hash, mime_type, "
                        + " title, created_at) "
                        + "VALUES (?, ?, ?, ?, 'text/markdown', ?, ?)",
                id, domain, "nh-" + id, "rh-" + id, "Snapshot " + id, "2026-01-01T00:00:00Z");
    }

    private void insertBuild(String id, String domain, String kbId, String status, String createdAt) {
        jdbc.update("INSERT INTO asset_builds "
                        + "(id, build_code, status, build_mode, domain, created_at, kb_id) "
                        + "VALUES (?, ?, ?, 'full', ?, ?, ?)",
                id, "B-" + id, status, domain, createdAt, kbId);
    }

    private void insertSelection(String buildId, String documentId, String snapshotId,
                                 String selectionStatus, String reason) {
        jdbc.update("INSERT INTO asset_build_document_snapshots "
                        + "(build_id, document_id, document_snapshot_id, selection_status, reason) "
                        + "VALUES (?, ?, ?, ?, ?)",
                buildId, documentId, snapshotId, selectionStatus, reason);
    }

    private boolean tableExists(String table) {
        Boolean present = jdbc.queryForObject(
                "SELECT to_regclass(?) IS NOT NULL", Boolean.class, table);
        return Boolean.TRUE.equals(present);
    }

    private boolean columnExists(String table, String column) {
        Integer count = jdbc.queryForObject(
                "SELECT count(*) FROM information_schema.columns "
                        + "WHERE table_name = ? AND column_name = ?",
                Integer.class, table, column);
        return count != null && count > 0;
    }
}
